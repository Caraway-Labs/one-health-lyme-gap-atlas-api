"""FastAPI application factory and versioned REST contract."""

import hashlib
import json
import re
import uuid
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from lyme_gap_atlas_shared import ScoreSettings
from lyme_gap_atlas_shared.observability import configure_logging, configure_tracing
from openai import OpenAI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import AuthenticatedUser, SupabaseTokenVerifier, TokenVerifier
from .config import ApiSettings, get_settings
from .knowledge_chat import (
    EVIDENCE_UNAVAILABLE,
    KnowledgeChatService,
    Neo4jRetriever,
    OpenAIAnswerer,
    SnowflakeBudgetStore,
)
from .middleware import KnowledgeChatLimitMiddleware, RateLimitMiddleware, RequestContextMiddleware
from .models import (
    AtlasMetadata,
    CountyDetail,
    KnowledgeChatRequest,
    KnowledgeChatResponse,
    ProblemDetails,
    ScoreCollection,
    UserProfileResponse,
    UserProfileWrite,
)
from .reports import (
    TEMPLATE_REGISTRY,
    CountyReport,
    PdfRenderer,
    RenderLimits,
    ReportService,
    StateReport,
)
from .reports.cache import PdfReportCache
from .reports.renderer import (
    RenderCompilationError,
    RendererFailure,
    RenderTimeout,
    ResourceLimitExceeded,
    UnknownTemplateError,
)
from .reports.renderers import TypstRenderer
from .profiles import ProfileStore, SupabaseProfileStore
from .repository import AtlasRepository, SnowflakeAtlasRepository
from .service import AtlasService


def _score_settings(
    ecological_share: Annotated[int, Query(ge=40, le=85, multiple_of=5)] = 65,
    low_incidence_breakpoint: Annotated[int, Query(ge=5, le=25)] = 10,
    missing_human_weakness: Annotated[int, Query(ge=40, le=90, multiple_of=5)] = 75,
) -> ScoreSettings:
    return ScoreSettings(
        ecological_share=ecological_share,
        low_incidence_breakpoint=low_incidence_breakpoint,
        missing_human_weakness=missing_human_weakness,
    )


def _etag(content: bytes) -> str:
    return f'"{hashlib.sha256(content).hexdigest()}"'


def _report_cache_key(report: CountyReport | StateReport) -> str:
    """Hash every report input except its artifact-generation timestamp."""

    content = report.model_dump(mode="json")
    content["identity"].pop("generated_at", None)
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _matches_etag(request: Request, etag: str) -> bool:
    return etag in {value.strip() for value in request.headers.get("if-none-match", "").split(",")}


def _filename_component(value: str) -> str:
    """Return an ASCII-only component safe for an HTTP attachment filename."""

    component = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return component or "unknown"


def create_app(
    repository: AtlasRepository | None = None,
    settings: ApiSettings | None = None,
    knowledge_chat_service: KnowledgeChatService | None = None,
    report_service: ReportService | None = None,
    pdf_renderer: PdfRenderer | None = None,
    profile_store: ProfileStore | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastAPI:
    config = settings or get_settings()
    configure_logging()
    configure_tracing("one-health-lyme-gap-atlas-api")
    service = AtlasService(repository or SnowflakeAtlasRepository(config), config.cache_ttl_seconds)
    reports = report_service or ReportService(service)
    renderer = pdf_renderer or TypstRenderer(RenderLimits.from_settings(config))
    pdf_cache = PdfReportCache(
        enabled=config.pdf_cache_enabled,
        ttl_seconds=config.pdf_cache_ttl_seconds,
        max_entries=config.pdf_cache_max_entries,
    )
    if knowledge_chat_service is None and config.knowledge_chat_enabled:
        neo4j_password = config.neo4j_runtime_password
        openai_key = config.openai_api_key
        hash_secret = config.kg_hash_secret
        if config.neo4j_uri and neo4j_password and openai_key and hash_secret:
            openai = OpenAI(api_key=openai_key.get_secret_value())
            knowledge_chat_service = KnowledgeChatService(
                Neo4jRetriever(
                    config.neo4j_uri,
                    config.neo4j_runtime_user,
                    neo4j_password.get_secret_value(),
                    openai,
                ),
                OpenAIAnswerer(openai, config.kg_chat_model),
                SnowflakeBudgetStore(config) if config.conversation_persistence_enabled else None,
                hash_secret.get_secret_value(),
            )
    app = FastAPI(
        title=config.app_name,
        version=config.app_version,
        description="Public API for Atlas data and reviewed knowledge-graph evidence chat.",
    )
    app.state.service = service
    accounts_configured = bool(
        config.supabase_url
        and config.supabase_secret_key
        and config.supabase_jwt_issuer
        and config.supabase_jwt_audience
    )
    configured_profile_store = profile_store or (
        SupabaseProfileStore(config) if accounts_configured else None
    )
    configured_token_verifier = token_verifier or (
        SupabaseTokenVerifier(config) if accounts_configured else None
    )
    app.add_middleware(GZipMiddleware, minimum_size=1_000)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=config.rate_limit_per_minute)
    app.add_middleware(KnowledgeChatLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    # Starlette applies the most recently added middleware first. Keep CORS outermost
    # so browser clients can read an error returned by a short-circuiting limiter.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "HEAD", "POST", "PUT", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type", "If-None-Match", "X-Request-ID"],
        expose_headers=["Content-Disposition", "ETag", "Retry-After", "X-Request-ID"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        problem = ProblemDetails(
            type="https://carawaylabs.com/problems/validation",
            title="Invalid request",
            status=422,
            detail="One or more request values are invalid.",
            instance=str(request.url.path),
            request_id=getattr(request.state, "request_id", "unavailable"),
            errors=json.loads(json.dumps(exc.errors(), default=str)),
        )
        return JSONResponse(
            problem.model_dump(mode="json"), status_code=422, media_type="application/problem+json"
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        problem = ProblemDetails(
            type=f"https://carawaylabs.com/problems/http-{exc.status_code}",
            title={404: "Not found", 429: "Too many requests", 503: "Service unavailable"}.get(
                exc.status_code, "Request failed"
            ),
            status=exc.status_code,
            detail=str(exc.detail),
            instance=str(request.url.path),
            request_id=getattr(request.state, "request_id", "unavailable"),
        )
        return JSONResponse(
            problem.model_dump(mode="json"),
            status_code=exc.status_code,
            media_type="application/problem+json",
            headers=exc.headers,
        )

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        try:
            graph_ready = (
                not config.knowledge_chat_enabled
                or knowledge_chat_service is not None
                and knowledge_chat_service.ready()
            )
            if service.ready() and graph_ready:
                return {"status": "ready"}
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="A required data service is unavailable"
            ) from exc
        raise HTTPException(status_code=503, detail="A required data service is unavailable")

    def authenticated_user(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthenticatedUser:
        if configured_token_verifier is None:
            raise _accounts_unavailable()
        return configured_token_verifier.verify(authorization)

    def _accounts_unavailable() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account features are temporarily unavailable.",
        )

    @app.get(
        "/v1/me/profile",
        response_model=UserProfileResponse,
        tags=["account"],
        responses={401: {"model": ProblemDetails}, 503: {"model": ProblemDetails}},
    )
    def get_profile(
        response: Response,
        user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> UserProfileResponse:
        if configured_profile_store is None:
            raise _accounts_unavailable()
        try:
            profile = configured_profile_store.get(user.user_id)
        except RuntimeError as exc:
            raise _accounts_unavailable() from exc
        response.headers["Cache-Control"] = "private, no-store"
        return UserProfileResponse(profile=profile)

    @app.put(
        "/v1/me/profile",
        response_model=UserProfileResponse,
        tags=["account"],
        responses={401: {"model": ProblemDetails}, 503: {"model": ProblemDetails}},
    )
    def save_profile(
        payload: UserProfileWrite,
        response: Response,
        user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> UserProfileResponse:
        if configured_profile_store is None:
            raise _accounts_unavailable()
        try:
            profile = configured_profile_store.save(user.user_id, payload)
        except RuntimeError as exc:
            raise _accounts_unavailable() from exc
        response.headers["Cache-Control"] = "private, no-store"
        return UserProfileResponse(profile=profile)

    @app.get("/v1/atlas/metadata", response_model=AtlasMetadata, tags=["atlas"])
    def metadata(response: Response, dataset_version: str | None = None) -> AtlasMetadata:
        try:
            result = service.metadata(dataset_version)
            response.headers["Cache-Control"] = "public, max-age=300"
            response.headers["ETag"] = _etag(result.model_dump_json().encode())
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Dataset release not found") from exc

    @app.get("/v1/atlas/geometry", tags=["atlas"])
    def geometry(request: Request, dataset_version: str | None = None) -> Response:
        try:
            payload = json.dumps(service.geometry(dataset_version), separators=(",", ":")).encode()
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Dataset release not found") from exc
        etag = _etag(payload)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)
        return Response(
            payload,
            media_type="application/geo+json",
            headers={"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/v1/atlas/scores", response_model=ScoreCollection, tags=["atlas"])
    def scores(
        response: Response,
        score_settings: Annotated[ScoreSettings, Depends(_score_settings)],
        dataset_version: str | None = None,
    ) -> ScoreCollection:
        try:
            result = service.scores(score_settings, dataset_version)
            response.headers["Cache-Control"] = "public, max-age=300"
            response.headers["ETag"] = _etag(result.model_dump_json().encode())
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Dataset release not found") from exc

    @app.get("/v1/counties/{fips}", response_model=CountyDetail, tags=["counties"])
    def county(
        response: Response,
        fips: Annotated[str, Path(pattern=r"^\d{5}$")],
        score_settings: Annotated[ScoreSettings, Depends(_score_settings)],
        dataset_version: str | None = None,
    ) -> CountyDetail:
        try:
            result = service.county(fips, score_settings, dataset_version)
            response.headers["Cache-Control"] = "public, max-age=300"
            response.headers["ETag"] = _etag(result.model_dump_json().encode())
            return result
        except (KeyError, LookupError) as exc:
            raise HTTPException(
                status_code=404, detail="County or dataset release not found"
            ) from exc

    @app.get(
        "/v1/counties/{fips}/report.pdf",
        response_class=Response,
        tags=["counties"],
        responses={
            200: {"content": {"application/pdf": {}}},
            404: {"model": ProblemDetails},
            413: {"model": ProblemDetails},
            422: {"model": ProblemDetails},
            503: {
                "model": ProblemDetails,
                "headers": {"Retry-After": {"schema": {"type": "string"}}},
            },
        },
    )
    def county_report_pdf(
        request: Request,
        fips: Annotated[str, Path(pattern=r"^\d{5}$")],
        score_settings: Annotated[ScoreSettings, Depends(_score_settings)],
        dataset_version: str | None = None,
        template: Annotated[str, Query(pattern=r"^[a-z]+-v\d+$")] = "county-v1",
    ) -> Response:
        template_definition = TEMPLATE_REGISTRY.get(template)
        if template_definition is None or template_definition.geography_level != "county":
            raise HTTPException(
                status_code=422, detail="The requested county report template is not available."
            )
        try:
            report = reports.county_report(fips, score_settings, dataset_version, template)
        except (KeyError, LookupError) as exc:
            raise HTTPException(
                status_code=404, detail="County or dataset release not found"
            ) from exc
        try:
            payload = pdf_cache.get_or_render(
                _report_cache_key(report), lambda: renderer.render(report, template)
            )
        except UnknownTemplateError as exc:
            raise HTTPException(
                status_code=422, detail="The requested county report template is not available."
            ) from exc
        except ResourceLimitExceeded as exc:
            raise HTTPException(
                status_code=413, detail="The report exceeds configured resource limits."
            ) from exc
        except RenderTimeout as exc:
            raise HTTPException(
                status_code=503,
                detail="Report rendering timed out. Please try again later.",
                headers={"Retry-After": "30"},
            ) from exc
        except (RenderCompilationError, RendererFailure) as exc:
            raise HTTPException(
                status_code=503,
                detail="The report renderer is temporarily unavailable.",
                headers={"Retry-After": "30"},
            ) from exc

        filename = "-".join(
            (
                "lyme-gap-atlas",
                _filename_component(report.geography.state_code),
                _filename_component(report.geography.name),
                _filename_component(report.geography.identifier),
                _filename_component(report.provenance.dataset_version),
            )
        )
        etag = _etag(payload)
        if _matches_etag(request, etag):
            return Response(
                status_code=304,
                headers={"Cache-Control": "public, max-age=300, must-revalidate", "ETag": etag},
            )
        return Response(
            payload,
            media_type="application/pdf",
            headers={
                "Cache-Control": "public, max-age=300, must-revalidate",
                "Content-Disposition": f'attachment; filename="{filename}.pdf"',
                "ETag": etag,
            },
        )

    @app.get(
        "/v1/states/{state}/report.pdf",
        response_class=Response,
        tags=["states"],
        responses={
            200: {"content": {"application/pdf": {}}},
            404: {"model": ProblemDetails},
            413: {"model": ProblemDetails},
            422: {"model": ProblemDetails},
            503: {
                "model": ProblemDetails,
                "headers": {"Retry-After": {"schema": {"type": "string"}}},
            },
        },
    )
    def state_report_pdf(
        request: Request,
        state: Annotated[str, Path(pattern=r"^[A-Z]{2}$")],
        score_settings: Annotated[ScoreSettings, Depends(_score_settings)],
        dataset_version: str | None = None,
        template: Annotated[str, Query(pattern=r"^[a-z]+-v\d+$")] = "state-v1",
    ) -> Response:
        template_definition = TEMPLATE_REGISTRY.get(template)
        if template_definition is None or template_definition.geography_level != "state":
            raise HTTPException(
                status_code=422, detail="The requested state report template is not available."
            )
        try:
            report = reports.state_report(state, score_settings, dataset_version, template)
        except (KeyError, LookupError) as exc:
            raise HTTPException(
                status_code=404, detail="State or dataset release not found"
            ) from exc
        try:
            payload = pdf_cache.get_or_render(
                _report_cache_key(report), lambda: renderer.render(report, template)
            )
        except UnknownTemplateError as exc:
            raise HTTPException(
                status_code=422, detail="The requested state report template is not available."
            ) from exc
        except ResourceLimitExceeded as exc:
            raise HTTPException(
                status_code=413, detail="The report exceeds configured resource limits."
            ) from exc
        except RenderTimeout as exc:
            raise HTTPException(
                status_code=503,
                detail="Report rendering timed out. Please try again later.",
                headers={"Retry-After": "30"},
            ) from exc
        except (RenderCompilationError, RendererFailure) as exc:
            raise HTTPException(
                status_code=503,
                detail="The report renderer is temporarily unavailable.",
                headers={"Retry-After": "30"},
            ) from exc

        filename = "-".join(
            (
                "lyme-gap-atlas",
                _filename_component(report.geography.state_code),
                _filename_component(report.provenance.dataset_version),
            )
        )
        etag = _etag(payload)
        if _matches_etag(request, etag):
            return Response(
                status_code=304,
                headers={"Cache-Control": "public, max-age=300, must-revalidate", "ETag": etag},
            )
        return Response(
            payload,
            media_type="application/pdf",
            headers={
                "Cache-Control": "public, max-age=300, must-revalidate",
                "Content-Disposition": f'attachment; filename="{filename}.pdf"',
                "ETag": etag,
            },
        )

    @app.get("/v1/atlas/ranking.csv", tags=["atlas"])
    def ranking_csv(
        score_settings: Annotated[ScoreSettings, Depends(_score_settings)],
        state: Annotated[str, Query(pattern=r"^(ALL|[A-Z]{2})$")] = "ALL",
        q: Annotated[str, Query(max_length=100)] = "",
        evidence: Literal["all", "ecological", "human", "complete"] = "all",
    ) -> Response:
        payload = service.ranking_csv(score_settings, state, q, evidence)
        filename = f"lyme-gap-atlas-ranking-{state.lower()}.csv"
        return Response(
            payload,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post(
        "/v1/knowledge-graph/chat",
        response_model=KnowledgeChatResponse,
        tags=["knowledge graph"],
        responses={
            429: {"model": ProblemDetails},
            503: {
                "model": KnowledgeChatResponse,
                "headers": {"Retry-After": {"schema": {"type": "string"}}},
            },
        },
    )
    def knowledge_graph_chat(
        request: Request, payload: KnowledgeChatRequest, response: Response
    ) -> KnowledgeChatResponse:
        if not config.knowledge_chat_enabled or knowledge_chat_service is None:
            response.status_code = 503
            response.headers["Retry-After"] = "60"
            return KnowledgeChatResponse(
                request_id=request.state.request_id,
                conversation_id=payload.conversation_id or str(uuid.uuid4()),
                configuration_version="kg-v1.0.0",
                status="evidence_unavailable",
                answer=EVIDENCE_UNAVAILABLE,
            )
        client = request.headers.get("do-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        try:
            result = knowledge_chat_service.chat(payload, request.state.request_id, client)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result.status in {"evidence_unavailable", "capacity_limited"}:
            response.status_code = 503
            response.headers["Retry-After"] = "30"
        return result

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live")
    return app


app = create_app()
