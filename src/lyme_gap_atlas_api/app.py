"""FastAPI application factory and versioned REST contract."""

import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from lyme_gap_atlas_kg import CONFIGURATION_VERSION
from lyme_gap_atlas_shared.domain import ScoreSettings
from lyme_gap_atlas_shared.observability import configure_logging, configure_tracing
from openai import OpenAI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.exceptions import HTTPException as StarletteHTTPException

from .ask_atlas_orchestration import (
    StructuredAssistant,
    StructuredAssistantRequest,
    StructuredAssistantResponse,
)
from .ask_atlas_tools import StructuredTools
from .assistant_policy import load_assistant_policy
from .auth import (
    AuthenticatedUser,
    SupabaseTokenVerifier,
    TokenVerifier,
    optional_authenticated_user,
)
from .auth_admin import AuthAdmin, AuthAdminError, SupabaseAuthAdmin
from .briefings import add_briefing_openapi
from .config import ApiSettings, get_settings
from .documentation import LOGO, install_documentation
from .environmental_reader_probe import start_reader_probe
from .feedback import (
    FEEDBACK_IDEMPOTENCY_MISMATCH_TYPE,
    FEEDBACK_PERSISTENCE_DETAIL,
    FEEDBACK_TOPOLOGY_UNSAFE_DETAIL,
    FeedbackIdempotencyMismatchError,
    FeedbackRejectedError,
    FeedbackService,
    FeedbackStore,
    FeedbackStoreError,
    SnowflakeFeedbackStore,
    feedback_process_topology_safe,
)
from .intelligence_feed import FeedRepository, FeedService, SnowflakeFeedRepository
from .intelligence_feed import router as intelligence_router
from .knowledge_chat import (
    EVIDENCE_UNAVAILABLE,
    KnowledgeChatService,
    Neo4jRetriever,
    OpenAIAnswerer,
    SnowflakeBudgetStore,
    SnowflakeCorpusProvenanceStore,
)
from .middleware import (
    FeedbackLimitMiddleware,
    KnowledgeChatLimitMiddleware,
    PrivacyRequestLimitMiddleware,
    PublicReadProtectionMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from .models import (
    AtlasMetadata,
    CountyDetail,
    FeedbackSubmissionRequest,
    FeedbackSubmissionResponse,
    KnowledgeChatRequest,
    KnowledgeChatResponse,
    PrivacyRequestConfirm,
    PrivacyRequestCreate,
    PrivacyRequestCreated,
    PrivacyRequestStatus,
    ProblemDetails,
    ScoreCollection,
    UserProfileResponse,
    UserProfileWrite,
)
from .privacy_requests import (
    InvalidPrivacyRequestError,
    PrivacyRequestNotFoundError,
    PrivacyRequestService,
    PrivacyRequestStore,
    PrivacyRequestStoreError,
    StaleSessionError,
    SupabasePrivacyRequestStore,
)
from .profiles import ProfileStore, ProfileStoreError, SupabaseProfileStore
from .public_contract import PublicQueryError
from .public_docs import (
    API_DESCRIPTION,
    API_SUMMARY,
    EMPTY_COLLECTION_EXAMPLE,
    LEGACY_ERRORS,
    MISSING_OBSERVATION_EXAMPLE,
    TAGS,
    problem_response,
)
from .public_metadata import MetadataRepository, MetadataService, SnowflakeMetadataRepository
from .public_observations import (
    ObservationRepository,
    ObservationService,
    SnowflakeObservationRepository,
)
from .public_openapi import public_projection
from .public_provenance import (
    ProvenanceRepository,
    ProvenanceService,
    SnowflakeProvenanceRepository,
)
from .public_routes import router as public_router
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
from .repository import AtlasDataUnavailableError, AtlasRepository, SnowflakeAtlasRepository
from .service import AtlasService
from .telemetry import PrivateInstrumentationProvider, server_request_hook, server_response_hook
from .telemetry_logging import operational_logger, operational_request_id, protect_dependency_logs

logger = operational_logger(__name__)


class AtlasFastAPI(FastAPI):
    _first_party_schema: dict[str, Any] | None = None

    def first_party_openapi(self) -> dict[str, Any]:
        """Complete codegen build artifact; never served by public docs or HTTP."""
        if self._first_party_schema is not None:
            return self._first_party_schema
        schema = get_openapi(
            title=self.title,
            version=self.version,
            openapi_version=self.openapi_version,
            summary="Complete first-party Atlas product contract",
            description=(
                "Build artifact for existing first-party Web client and validator "
                "generation. Not an external developer contract or HTTP surface. "
                "Use the public openapi.json for external documentation."
            ),
            terms_of_service=self.terms_of_service,
            contact=self.contact,
            license_info=self.license_info,
            routes=self.routes,
            webhooks=self.webhooks.routes,
            tags=self.openapi_tags,
            servers=self.servers,
            separate_input_output_schemas=self.separate_input_output_schemas,
            external_docs=self.openapi_external_docs,
        )
        if "BriefingArtifact" not in schema.get("components", {}).get("schemas", {}):
            add_briefing_openapi(schema)
        # Problem responses use their actual media type, without an extra JSON
        # response model. Generate the referenced component directly from Pydantic.
        schema["components"]["schemas"]["ProblemDetails"] = ProblemDetails.model_json_schema()
        schema["info"]["x-logo"] = deepcopy(LOGO)
        # FastAPI's OpenAPI serialization drops nulls inside response examples.
        # Restore these meaningful missingness/pagination values after serialization.
        schema["paths"]["/v1/observations"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["examples"] = deepcopy(
            {
                "empty": EMPTY_COLLECTION_EXAMPLE,
                "missing": MISSING_OBSERVATION_EXAMPLE,
            }
        )
        self._first_party_schema = schema
        return schema

    def openapi(self) -> dict[str, Any]:
        """Canonical public HTTP schema; cache separately from first-party codegen."""
        if self.openapi_schema is None:
            self.openapi_schema = public_projection(self.first_party_openapi())
        return self.openapi_schema


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
    privacy_request_store: PrivacyRequestStore | None = None,
    auth_admin: AuthAdmin | None = None,
    feedback_store: FeedbackStore | None = None,
    metadata_repository: MetadataRepository | None = None,
    observation_repository: ObservationRepository | None = None,
    provenance_repository: ProvenanceRepository | None = None,
    intelligence_repository: FeedRepository | None = None,
) -> FastAPI:
    config = settings or get_settings()
    configure_logging()
    protect_dependency_logs()
    configure_tracing("one-health-lyme-gap-atlas-api")
    logger.info("atlas_runtime_configuration", extra={"context": {"telemetry_schema_version": "1"}})
    logger.info(
        "knowledge_chat_runtime_configuration",
        extra={
            "context": {
                "enabled": config.knowledge_chat_enabled,
                "provider": "openai",
                "configuration_version": CONFIGURATION_VERSION,
                "app_version": config.app_version,
            }
        },
    )
    feedback_topology_safe = feedback_process_topology_safe()
    if not feedback_topology_safe:
        logger.error(
            "feedback_topology_unsafe",
            extra={
                "context": {
                    "reason": "WEB_CONCURRENCY_or_UVICORN_WORKERS_gt_1",
                }
            },
        )
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
                SnowflakeCorpusProvenanceStore(config),
                deadline_seconds=config.kg_chat_deadline_seconds,
                generation_timeout_seconds=config.kg_generation_timeout_seconds,
                snowflake_settings=config,
            )
    probe_started = False

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        nonlocal probe_started
        if (
            not probe_started
            and repository is None
            and metadata_repository is None
            and observation_repository is None
        ):
            probe_started = True
            start_reader_probe(config)
        yield

    app = AtlasFastAPI(
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
        title=config.app_name,
        version=config.app_version,
        description=API_DESCRIPTION,
        summary=API_SUMMARY,
        contact={"name": "Caraway Labs", "url": "https://carawaylabs.com"},
        openapi_tags=TAGS,
        servers=[{"url": "https://api.carawaylabs.com", "description": "Production"}],
        openapi_external_docs={
            "description": "Atlas documentation and developer guides",
            "url": "https://carawaylabs.com/docs",
        },
    )
    app.state.service = service
    install_documentation(app)
    app.state.public_settings = config
    app.state.metadata_service = MetadataService(
        metadata_repository or SnowflakeMetadataRepository(config)
    )
    app.state.observation_service = ObservationService(
        observation_repository or SnowflakeObservationRepository(config)
    )
    app.state.provenance_service = ProvenanceService(
        provenance_repository or SnowflakeProvenanceRepository(config)
    )
    app.include_router(public_router)
    app.state.intelligence_feed_service = FeedService(
        intelligence_repository or SnowflakeFeedRepository(config)
    )
    app.include_router(intelligence_router)
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
    configured_privacy_store = privacy_request_store or (
        SupabasePrivacyRequestStore(config) if accounts_configured else None
    )
    configured_auth_admin = auth_admin or (
        SupabaseAuthAdmin(config) if accounts_configured else None
    )
    configured_feedback_store = feedback_store or SnowflakeFeedbackStore(config)
    feedback_service = FeedbackService(configured_feedback_store)
    privacy_request_service = (
        PrivacyRequestService(
            configured_privacy_store,
            configured_profile_store,
            configured_auth_admin,
            feedback_store=configured_feedback_store,
        )
        if configured_privacy_store and configured_profile_store and configured_auth_admin
        else None
    )
    app.add_middleware(GZipMiddleware, minimum_size=1_000)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=config.rate_limit_per_minute)
    app.add_middleware(
        PublicReadProtectionMiddleware,
        requests_per_minute=config.public_rate_limit_per_minute,
        concurrent_requests=config.public_concurrent_requests_per_ip,
        max_query_bytes=config.public_max_query_bytes,
    )
    app.add_middleware(KnowledgeChatLimitMiddleware)
    app.add_middleware(FeedbackLimitMiddleware)
    app.add_middleware(PrivacyRequestLimitMiddleware)
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
        is_canonical = request.url.path in {
            "/v1/indicators",
            "/v1/measures",
            "/v1/observations",
            "/v1/sources",
        } or request.url.path.startswith(
            (
                "/v1/indicators/",
                "/v1/measures/",
                "/v1/geographies/",
                "/v1/sources/",
                "/v1/methodologies/",
            )
        )
        response_status = 400 if is_canonical else 422
        problem = ProblemDetails(
            type="https://carawaylabs.com/problems/validation",
            title="Invalid request",
            status=response_status,
            detail="One or more request values are invalid.",
            instance=str(request.url.path),
            request_id=getattr(request.state, "request_id", "unavailable"),
            errors=json.loads(json.dumps(exc.errors(), default=str)),
            code="INVALID_REQUEST" if is_canonical else None,
        )
        return JSONResponse(
            problem.model_dump(mode="json", exclude=set() if is_canonical else {"code"}),
            status_code=response_status,
            media_type="application/problem+json",
        )

    @app.exception_handler(PublicQueryError)
    async def public_query_error(request: Request, exc: PublicQueryError) -> JSONResponse:
        response_status = 404 if exc.code == "RESOURCE_NOT_FOUND" else 400
        if exc.code == "QUERY_TOO_BROAD":
            logger.info("public_read_rejected", extra={"context": {"reason": "query_too_broad"}})
        problem = ProblemDetails(
            type=f"https://carawaylabs.com/problems/{exc.code.lower().replace('_', '-')}",
            title="Invalid public query",
            status=response_status,
            detail=str(exc),
            instance=request.url.path,
            request_id=getattr(request.state, "request_id", "unavailable"),
            code=exc.code,
        )
        return JSONResponse(
            problem.model_dump(mode="json"),
            status_code=response_status,
            media_type="application/problem+json",
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
            code=(
                "RESOURCE_NOT_FOUND"
                if exc.status_code == 404
                and request.url.path.startswith(
                    (
                        "/v1/indicators/",
                        "/v1/measures/",
                        "/v1/geographies/",
                        "/v1/sources/",
                        "/v1/methodologies/",
                    )
                )
                else "CANONICAL_DATA_UNAVAILABLE"
                if exc.status_code == 503
                and str(exc.detail) == "Canonical data delivery is not yet available."
                else None
            ),
        )
        return JSONResponse(
            problem.model_dump(mode="json", exclude={"code"} if problem.code is None else set()),
            status_code=exc.status_code,
            media_type="application/problem+json",
            headers=exc.headers,
        )

    @app.exception_handler(AtlasDataUnavailableError)
    async def atlas_data_unavailable(
        request: Request, exc: AtlasDataUnavailableError
    ) -> JSONResponse:
        return await http_error(
            request,
            HTTPException(
                status_code=503,
                detail="The governed Atlas data service is temporarily unavailable.",
            ),
        )

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        try:
            # Process readiness is Snowflake (+ chat wiring when enabled).
            # Neo4j is probed at chat time and fails closed per ADR 0007; do not
            # block App Platform deploys on a private Bolt probe that can hang.
            # Feedback idempotency is process-local; multi-worker topology fails closed.
            chat_wired = not config.knowledge_chat_enabled or knowledge_chat_service is not None
            if not feedback_topology_safe:
                raise HTTPException(
                    status_code=503,
                    detail=FEEDBACK_TOPOLOGY_UNSAFE_DETAIL,
                )
            if service.ready() and chat_wired:
                return {"status": "ready"}
        except HTTPException:
            raise
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

    def resolve_optional_user(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthenticatedUser | None:
        return optional_authenticated_user(authorization, configured_token_verifier)

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
        request: Request,
        response: Response,
        user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> UserProfileResponse:
        if configured_profile_store is None:
            raise _accounts_unavailable()
        try:
            profile = configured_profile_store.get(user.user_id)
        except ProfileStoreError as exc:
            logger.warning(
                "account_profile_read_failed",
                extra={
                    "context": {
                        "request_id": getattr(request.state, "request_id", "unavailable"),
                        "operation": exc.operation,
                        "failure_category": exc.category,
                        "upstream_status": exc.upstream_status,
                    }
                },
            )
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
        request: Request,
        response: Response,
        user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> UserProfileResponse:
        if configured_profile_store is None:
            raise _accounts_unavailable()
        try:
            profile = configured_profile_store.save(user.user_id, payload)
        except ProfileStoreError as exc:
            logger.warning(
                "account_profile_save_failed",
                extra={
                    "context": {
                        "request_id": getattr(request.state, "request_id", "unavailable"),
                        "operation": exc.operation,
                        "failure_category": exc.category,
                        "upstream_status": exc.upstream_status,
                    }
                },
            )
            raise _accounts_unavailable() from exc
        response.headers["Cache-Control"] = "private, no-store"
        return UserProfileResponse(profile=profile)

    def _privacy_service() -> PrivacyRequestService:
        if privacy_request_service is None:
            raise _accounts_unavailable()
        return privacy_request_service

    def _privacy_http_error(
        exc: InvalidPrivacyRequestError | PrivacyRequestNotFoundError | StaleSessionError,
    ) -> HTTPException:
        if isinstance(exc, StaleSessionError):
            return HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A recently signed-in session is required to continue.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if isinstance(exc, PrivacyRequestNotFoundError):
            return HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Privacy request not found."
            )
        if exc.reason == "export_unavailable":
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Export is not available."
            )
        if exc.reason == "invalid_state":
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This privacy request cannot be confirmed.",
            )
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The confirmation is invalid or has expired.",
        )

    @app.post(
        "/v1/me/privacy-requests",
        response_model=PrivacyRequestCreated,
        tags=["account"],
        responses={401: {"model": ProblemDetails}, 503: {"model": ProblemDetails}},
    )
    def create_privacy_request(
        payload: PrivacyRequestCreate,
        request: Request,
        response: Response,
        user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> PrivacyRequestCreated:
        service = _privacy_service()
        try:
            created = service.create(user.user_id, payload.action)
        except PrivacyRequestStoreError as exc:
            logger.warning(
                "privacy_request_create_failed",
                extra={
                    "context": {
                        "request_id": getattr(request.state, "request_id", "unavailable"),
                        "action": payload.action,
                        "failure_category": exc.category,
                    }
                },
            )
            raise _accounts_unavailable() from exc
        response.headers["Cache-Control"] = "private, no-store"
        return created

    @app.post(
        "/v1/me/privacy-requests/{request_id}/confirm",
        response_model=PrivacyRequestStatus,
        tags=["account"],
        responses={
            400: {"model": ProblemDetails},
            401: {"model": ProblemDetails},
            404: {"model": ProblemDetails},
            409: {"model": ProblemDetails},
            503: {"model": ProblemDetails},
        },
    )
    def confirm_privacy_request(
        request_id: Annotated[uuid.UUID, Path()],
        payload: PrivacyRequestConfirm,
        request: Request,
        response: Response,
        user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> PrivacyRequestStatus:
        service = _privacy_service()
        try:
            result = service.confirm(request_id, user.user_id, payload.nonce, user.issued_at)
        except (InvalidPrivacyRequestError, PrivacyRequestNotFoundError, StaleSessionError) as exc:
            raise _privacy_http_error(exc) from exc
        except (PrivacyRequestStoreError, AuthAdminError) as exc:
            logger.warning(
                "privacy_request_confirm_failed",
                extra={
                    "context": {
                        "request_id": operational_request_id(),
                        "failure_category": exc.category,
                    }
                },
            )
            raise _accounts_unavailable() from exc
        response.headers["Cache-Control"] = "private, no-store"
        return result

    @app.get(
        "/v1/me/privacy-requests/{request_id}",
        response_model=PrivacyRequestStatus,
        tags=["account"],
        responses={
            401: {"model": ProblemDetails},
            404: {"model": ProblemDetails},
            503: {"model": ProblemDetails},
        },
    )
    def get_privacy_request(
        request_id: Annotated[uuid.UUID, Path()],
        response: Response,
        user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> PrivacyRequestStatus:
        service = _privacy_service()
        try:
            result = service.status(request_id, user.user_id)
        except PrivacyRequestNotFoundError as exc:
            raise _privacy_http_error(exc) from exc
        except PrivacyRequestStoreError as exc:
            logger.warning(
                "privacy_request_status_failed",
                extra={
                    "context": {
                        "request_id": operational_request_id(),
                        "failure_category": exc.category,
                    }
                },
            )
            raise _accounts_unavailable() from exc
        response.headers["Cache-Control"] = "private, no-store"
        return result

    @app.get(
        "/v1/me/privacy-requests/{request_id}/export",
        tags=["account"],
        responses={
            401: {"model": ProblemDetails},
            404: {"model": ProblemDetails},
            409: {"model": ProblemDetails},
            503: {"model": ProblemDetails},
        },
    )
    def download_privacy_export(
        request_id: Annotated[uuid.UUID, Path()],
        user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> JSONResponse:
        service = _privacy_service()
        try:
            payload = service.export_payload(request_id, user.user_id)
        except (InvalidPrivacyRequestError, PrivacyRequestNotFoundError, StaleSessionError) as exc:
            raise _privacy_http_error(exc) from exc
        except PrivacyRequestStoreError as exc:
            logger.warning(
                "privacy_export_download_failed",
                extra={
                    "context": {
                        "request_id": operational_request_id(),
                        "failure_category": exc.category,
                    }
                },
            )
            raise _accounts_unavailable() from exc
        return JSONResponse(
            payload,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": 'attachment; filename="atlas-user-data-export.json"',
            },
        )

    @app.get(
        "/v1/atlas/metadata",
        response_model=AtlasMetadata,
        tags=["atlas"],
        operation_id="metadata_v1_atlas_metadata_get",
        summary="Get Atlas release metadata",
        description=(
            "Release metadata, source freshness and methodology; dataset_version selects an "
            "existing release. Cached publicly for 300 seconds with ETag. ETag is "
            "informational here; this route does not implement conditional 304."
        ),
        responses=LEGACY_ERRORS,
    )
    def metadata(response: Response, dataset_version: str | None = None) -> AtlasMetadata:
        try:
            result = service.metadata(dataset_version)
            response.headers["Cache-Control"] = "public, max-age=300"
            response.headers["ETag"] = _etag(result.model_dump_json().encode())
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Dataset release not found") from exc

    @app.get(
        "/v1/atlas/geometry",
        tags=["atlas"],
        description=(
            "Existing generalized CDC/ATSDR SVI 2022 display geometry in EPSG:4326, "
            "identified by stable five-digit county FIPS. This resource never returns "
            "internal 2025 TIGER/Line analysis polygons used for raster aggregation."
        ),
        operation_id="geometry_v1_atlas_geometry_get",
        summary="Get county display geometry",
        response_class=Response,
        responses={
            **LEGACY_ERRORS,
            200: {
                "description": "Display GeoJSON, EPSG:4326; immutable TTL 31536000; ETag.",
                "content": {"application/geo+json": {"schema": {"type": "object"}}},
            },
            304: {"description": "Matching If-None-Match; empty body."},
        },
    )
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

    @app.get(
        "/v1/atlas/scores",
        response_model=ScoreCollection,
        tags=["atlas"],
        operation_id="scores_v1_atlas_scores_get",
        summary="Get county gap scores",
        description=(
            "Existing surveillance-gap scores and settings for an optional dataset_version. "
            "Scores are not individual risk estimates or diagnoses. Public cache TTL 300 "
            "seconds with informational ETag; no conditional 304."
        ),
        responses=LEGACY_ERRORS,
    )
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

    @app.get(
        "/v1/counties/{fips}",
        response_model=CountyDetail,
        tags=["counties"],
        operation_id="county_v1_counties__fips__get",
        summary="Get county analytics and provenance",
        description=(
            "Five-digit county FIPS with leading zeros retained. Includes release/source "
            "metadata and limitations. Optional dataset_version and score settings retain "
            "existing meanings. Public cache TTL 300 seconds with informational ETag; no "
            "conditional 304."
        ),
        responses=LEGACY_ERRORS,
    )
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
            **LEGACY_ERRORS,
            200: {
                "description": "PDF attachment.",
                "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
            },
            304: {"description": "Matching If-None-Match; empty body."},
            413: problem_response(413, None, "The report exceeds configured resource limits."),
        },
        operation_id="county_report_pdf_v1_counties__fips__report_pdf_get",
        summary="Download a county report",
        description=(
            "PDF attachment using the immutable county-v1 registered template by default. "
            "Optional dataset_version and score settings bind report inputs. "
            "ETag/If-None-Match supports 304; public cache TTL 300 seconds with "
            "revalidation. Unknown templates return 422; resource limits 413; renderer "
            "failure/timeout 503."
        ),
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
            **LEGACY_ERRORS,
            200: {
                "description": "PDF attachment.",
                "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
            },
            304: {"description": "Matching If-None-Match; empty body."},
            413: problem_response(413, None, "The report exceeds configured resource limits."),
        },
        operation_id="state_report_pdf_v1_states__state__report_pdf_get",
        summary="Download a state report",
        description=(
            "PDF attachment using the immutable state-v1 registered template by default. "
            "State is a two-letter code. Optional dataset_version and score settings bind "
            "report inputs. ETag/If-None-Match supports 304; public cache TTL 300 seconds "
            "with revalidation. Unknown templates return 422; resource limits 413; renderer "
            "failure/timeout 503."
        ),
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

    @app.get(
        "/v1/atlas/ranking.csv",
        tags=["atlas"],
        operation_id="ranking_csv_v1_atlas_ranking_csv_get",
        summary="Download filtered county rankings",
        description=(
            "Filtered CSV attachment for current county rankings. Existing state, text and "
            "evidence filters and score settings apply; no pagination. This route does not "
            "declare a public cache TTL."
        ),
        response_class=Response,
        responses={
            **LEGACY_ERRORS,
            200: {
                "description": "CSV attachment.",
                "content": {"text/csv": {"schema": {"type": "string"}}},
            },
        },
    )
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
        "/v1/feedback",
        response_model=FeedbackSubmissionResponse,
        tags=["feedback"],
        responses={
            401: {"model": ProblemDetails},
            409: {"model": ProblemDetails},
            413: {"model": ProblemDetails},
            415: {"model": ProblemDetails},
            422: {"model": ProblemDetails},
            429: {"model": ProblemDetails},
            503: {"model": ProblemDetails},
        },
    )
    def submit_feedback(
        payload: FeedbackSubmissionRequest,
        request: Request,
        response: Response,
        user: Annotated[AuthenticatedUser | None, Depends(resolve_optional_user)],
    ) -> FeedbackSubmissionResponse | JSONResponse:
        request_id = getattr(request.state, "request_id", "unavailable")
        response.headers["Cache-Control"] = "no-store"
        if not feedback_topology_safe:
            logger.info(
                "feedback_submission",
                extra={
                    "context": {
                        "outcome": "unexpected",
                        "category": payload.category,
                        "route_id": payload.route_id,
                        "request_id": request_id,
                    }
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=FEEDBACK_TOPOLOGY_UNSAFE_DETAIL,
            )
        account_id = user.user_id if user is not None else None
        try:
            result = feedback_service.submit(payload, account_id)
        except FeedbackIdempotencyMismatchError:
            logger.info(
                "feedback_submission",
                extra={
                    "context": {
                        "outcome": "rejected",
                        "category": payload.category,
                        "route_id": payload.route_id,
                        "request_id": request_id,
                    }
                },
            )
            problem = ProblemDetails(
                type=FEEDBACK_IDEMPOTENCY_MISMATCH_TYPE,
                title="Feedback idempotency mismatch",
                status=409,
                detail=(
                    "This submission token was already used with a different payload. "
                    "Start a new report to continue."
                ),
                instance=str(request.url.path),
                request_id=request_id,
            )
            return JSONResponse(
                problem.model_dump(mode="json"),
                status_code=409,
                media_type="application/problem+json",
                headers={"Cache-Control": "no-store"},
            )
        except FeedbackRejectedError as exc:
            logger.info(
                "feedback_submission",
                extra={
                    "context": {
                        "outcome": "rejected",
                        "category": payload.category,
                        "route_id": payload.route_id,
                        "request_id": request_id,
                    }
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=FEEDBACK_PERSISTENCE_DETAIL,
            ) from exc
        except FeedbackStoreError as exc:
            logger.warning(
                "feedback_submission",
                extra={
                    "context": {
                        "outcome": "persistence_failed",
                        "category": payload.category,
                        "route_id": payload.route_id,
                        "request_id": request_id,
                    }
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=FEEDBACK_PERSISTENCE_DETAIL,
            ) from exc
        except Exception as exc:
            logger.exception(
                "feedback_submission",
                extra={
                    "context": {
                        "outcome": "unexpected",
                        "category": payload.category,
                        "route_id": payload.route_id,
                        "request_id": request_id,
                    }
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=FEEDBACK_PERSISTENCE_DETAIL,
            ) from exc
        logger.info(
            "feedback_submission",
            extra={
                "context": {
                    "outcome": "replayed" if result.replayed else "accepted",
                    "category": payload.category,
                    "route_id": payload.route_id,
                    "request_id": request_id,
                }
            },
        )
        return result

    @app.post(
        "/v1/assistant/structured",
        response_model=StructuredAssistantResponse,
        operation_id="askAtlasStructured",
        summary="Ask Atlas over governed structured evidence",
        description=(
            "Internal Structured-mode Assistant boundary. Answers include cited claims and "
            "the bounded typed tool evidence used to form them. Literature and Both are "
            "reserved for their governed capability; no arbitrary query is accepted."
        ),
        tags=["assistant"],
    )
    def ask_atlas_structured(
        request: Request, payload: StructuredAssistantRequest
    ) -> StructuredAssistantResponse:
        tools = StructuredTools(
            request.app.state.metadata_service,
            request.app.state.observation_service,
            request.app.state.provenance_service,
        )
        return StructuredAssistant(tools).ask(payload)

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
                assistant_policy_version=load_assistant_policy().version,
                status="evidence_unavailable",
                answer=EVIDENCE_UNAVAILABLE,
                evidence_state="evidence_unavailable",
                source_used="literature_evidence",
            )
        client = request.headers.get("do-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        request.state.knowledge_chat_diagnostics = {}
        try:
            result = knowledge_chat_service.chat(
                payload,
                request.state.request_id,
                client,
                request.state.knowledge_chat_diagnostics,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result.status in {"evidence_unavailable", "capacity_limited"}:
            response.status_code = 503
            response.headers["Retry-After"] = "30"
        return result

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=PrivateInstrumentationProvider(),
        server_request_hook=server_request_hook,
        client_response_hook=server_response_hook,
        exclude_spans=["receive"],
    )
    return app


app = create_app()
