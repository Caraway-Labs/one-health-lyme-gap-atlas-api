"""FastAPI application factory and versioned REST contract."""

import asyncio
import hashlib
import json
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date
from typing import Annotated, Any, Literal

import anyio.to_thread
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

from .ask_atlas_mixed_composition import MixedAssistantResponse
from .ask_atlas_mixed_service import MixedAssistant, MixedAssistantRequest
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
from .cdc_surveillance import CDCObservationAdapter
from .config import ApiSettings, get_settings
from .county_context_probe import start_county_context_probe
from .county_review import CountyEvidence, StateReview, aggregate_state, load_config
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
    OpenAIConnectionWarmer,
    SnowflakeBudgetStore,
    SnowflakeCorpusProvenanceStore,
    build_openai_http_client,
    warm_openai_sdk,
)
from .middleware import (
    FeedbackLimitMiddleware,
    KnowledgeChatLimitMiddleware,
    PrivacyRequestLimitMiddleware,
    PublicReadProtectionMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from .model_serving_probe import start_model_serving_probe
from .models import (
    AtlasMetadata,
    CountyDetail,
    FeedbackSubmissionRequest,
    FeedbackSubmissionResponse,
    KnowledgeChatRequest,
    KnowledgeChatResponse,
    MapValuesCollection,
    PrivacyRequestConfirm,
    PrivacyRequestCreate,
    PrivacyRequestCreated,
    PrivacyRequestStatus,
    ProblemDetails,
    ScoreCollection,
    UserProfileResponse,
    UserProfileWrite,
)
from .openapi_nullable import normalize_nullable_types
from .openapi_params import (
    CountyFipsAsciiPath,
    CountyFipsPath,
    DatasetVersion,
    EcologicalShare,
    LowIncidenceBreakpoint,
    MissingHumanWeakness,
    SmoothK,
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
    DESCRIPTION_400_REPORT,
    DESCRIPTION_404,
    EXAMPLE_QUERY_TOO_BROAD,
    LEGACY_ERRORS,
    REPORT_TOO_LARGE_RESPONSE,
    REPORT_UNAVAILABLE_RESPONSE,
    TAGS,
    apply_response_examples,
    apply_response_headers,
    problem_response,
    problem_type_uri,
    route_problem_instances,
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
from .schema_descriptions import apply_schema_descriptions
from .service import AtlasService
from .snowflake_monitor import ReadinessDependencies, ReadinessResponse, SnowflakeMonitor
from .snowflake_read_session import get_read_session_pool
from .snowflake_write_session import get_write_session_pool
from .surveillance_delivery import SurveillanceResolver
from .surveillance_situations import (
    CountySurveillanceSituations,
    SituationRepository,
    SnowflakeSituationRepository,
)
from .telemetry import PrivateInstrumentationProvider, server_request_hook, server_response_hook
from .telemetry_logging import operational_logger, operational_request_id, protect_dependency_logs
from .tier1_priority import (
    SnowflakeTier1PriorityRepository,
    Tier1CountyPriority,
    Tier1CountyPriorityAvailability,
    Tier1PriorityRepository,
    Tier1PriorityService,
)
from .tier1_timing import Tier1Timing, tier1_request_timing
from .tier1_timing import phase as tier1_phase

logger = operational_logger(__name__)


def _canonical_validation_is_400(path: str) -> bool:
    return path in {
        "/v1/indicators",
        "/v1/measures",
        "/v1/observations",
        "/v1/sources",
    } or path.startswith(
        (
            "/v1/indicators/",
            "/v1/measures/",
            "/v1/geographies/",
            "/v1/sources/",
            "/v1/methodologies/",
        )
    )


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
        # Keep existing component names stable for generated clients while
        # making both observation collection alternatives discoverable. They
        # remain anyOf: period county-context rows can satisfy both schemas.
        schemas = schema["components"]["schemas"]
        apply_schema_descriptions(schemas)
        # FastAPI normally adds a generic 422 response to query-parameter
        # operations. The shared canonical response map lists every status
        # these routes can actually return, and their validation handler
        # normalizes invalid requests to 400. Remove only that generated 422
        # from those fully specified maps; unrelated operations keep FastAPI's
        # validation response.
        canonical_error_statuses = {"400", "404", "413", "414", "429", "503"}
        for path, path_item in schema["paths"].items():
            if not _canonical_validation_is_400(path):
                continue
            # The geography identity route no longer has a 503 (ADR 0021): its unpublished
            # state is a typed 200, so 503 is not required for the full-map check.
            required = canonical_error_statuses
            if path == "/v1/geographies/{geography_type}/{geography_id}":
                required = canonical_error_statuses - {"503"}
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                responses = operation.get("responses", {})
                if required <= responses.keys():
                    responses.pop("422", None)
        if "BriefingArtifact" not in schema.get("components", {}).get("schemas", {}):
            add_briefing_openapi(schema)
        # Problem responses use their actual media type, without an extra JSON
        # response model. Generate the referenced component directly from Pydantic.
        schema["components"]["schemas"]["ProblemDetails"] = ProblemDetails.model_json_schema()
        route_problem_instances(schema)
        normalize_nullable_types(schema)
        schema["info"]["x-logo"] = deepcopy(LOGO)
        apply_response_examples(schema)
        apply_response_headers(schema)
        self._first_party_schema = schema
        return schema

    def openapi(self) -> dict[str, Any]:
        """Canonical public HTTP schema; cache separately from first-party codegen."""
        if self.openapi_schema is None:
            self.openapi_schema = public_projection(self.first_party_openapi())
        return self.openapi_schema


def _score_settings(
    ecological_share: EcologicalShare = 65,
    smooth_k: SmoothK = 50,
    missing_human_weakness: MissingHumanWeakness = 75,
    low_incidence_breakpoint: LowIncidenceBreakpoint = None,
) -> ScoreSettings:
    if low_incidence_breakpoint is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Legacy low_incidence_breakpoint is incompatible with Surveillance Gap Score v1.1; "
                "refresh the client and use smooth_k (governed default 50)."
            ),
        )
    return ScoreSettings(
        ecological_share=ecological_share,
        smooth_k=smooth_k,
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


def _atlas_brief_filename(report: CountyReport) -> str:
    """Short County_State_AtlasBrief name. Release identifiers stay inside the PDF."""

    county = re.sub(r"\s+County$", "", report.geography.name.strip(), flags=re.IGNORECASE)
    ascii_county = unicodedata.normalize("NFKD", county).encode("ascii", "ignore").decode("ascii")
    token = re.sub(r"[^A-Za-z0-9]+", "_", ascii_county).strip("_") or "County"
    state = re.sub(r"[^A-Za-z]", "", report.geography.state_code).upper() or "XX"
    return f"{token}_{state}_AtlasBrief"


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
    tier1_priority_repository: Tier1PriorityRepository | None = None,
    surveillance_resolver: SurveillanceResolver | None = None,
    situation_repository: SituationRepository | None = None,
) -> FastAPI:
    config = settings or get_settings()
    situations = situation_repository or SnowflakeSituationRepository(config)
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
    service = AtlasService(
        repository or SnowflakeAtlasRepository(config),
        config.cache_ttl_seconds,
        max_stale_seconds=config.snapshot_max_stale_seconds,
        refresh_backoff_seconds=config.snapshot_refresh_backoff_seconds,
    )
    tier1_priority = Tier1PriorityService(
        tier1_priority_repository or SnowflakeTier1PriorityRepository(config)
    )
    reports = report_service or ReportService(service)
    renderer = pdf_renderer or TypstRenderer(RenderLimits.from_settings(config))
    pdf_cache = PdfReportCache(
        enabled=config.pdf_cache_enabled,
        ttl_seconds=config.pdf_cache_ttl_seconds,
        max_entries=config.pdf_cache_max_entries,
    )
    openai_warmer: OpenAIConnectionWarmer | None = None
    if knowledge_chat_service is None and config.knowledge_chat_enabled:
        neo4j_password = config.neo4j_runtime_password
        openai_key = config.openai_api_key
        hash_secret = config.kg_hash_secret
        if config.neo4j_uri and neo4j_password and openai_key and hash_secret:
            openai_http = build_openai_http_client()
            openai = OpenAI(api_key=openai_key.get_secret_value(), http_client=openai_http)
            openai_warmer = OpenAIConnectionWarmer(openai_http, f"{openai.base_url}models")
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
                persist_in_background=True,
                prefetch_provenance=True,
            )
    snowflake_monitor = SnowflakeMonitor(config, service.ready if repository is not None else None)
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
            start_county_context_probe(config)
            start_model_serving_probe(config)
        snowflake_monitor.start()
        try:
            await asyncio.to_thread(
                snowflake_monitor.initial_check.wait, config.snowflake_startup_check_timeout_seconds
            )
            if openai_warmer is not None:
                await anyio.to_thread.run_sync(warm_openai_sdk)
                openai_warmer.start()
            yield
        finally:
            await asyncio.to_thread(snowflake_monitor.stop)
            if openai_warmer is not None:
                openai_warmer.stop()
            await anyio.to_thread.run_sync(get_read_session_pool().close_all)
            await anyio.to_thread.run_sync(get_write_session_pool().close_all)

    app = AtlasFastAPI(
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
        title=config.app_name,
        version=config.app_version,
        description=API_DESCRIPTION,
        summary=API_SUMMARY,
        contact={"name": "One Health Atlas", "url": "https://onehealthatlas.org/"},
        openapi_tags=TAGS,
        servers=[{"url": "https://api.onehealthatlas.org", "description": "Production"}],
        openapi_external_docs={
            "description": "Atlas documentation and developer guides",
            "url": "https://onehealthatlas.org/docs",
        },
    )
    app.state.service = service
    install_documentation(app)
    app.state.public_settings = config
    app.state.metadata_service = MetadataService(
        metadata_repository or SnowflakeMetadataRepository(config)
    )
    app.state.observation_service = ObservationService(
        observation_repository or SnowflakeObservationRepository(config),
        surveillance_resolver,
        CDCObservationAdapter(config) if config.cdc_surveillance_enabled else None,
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

    @app.middleware("http")
    async def withhold_public_cache_while_stale(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        # A stale-while-error snapshot must not be cached downstream as if it were fresh.
        if service.is_stale and response.headers.get("cache-control", "").startswith("public"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

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
        is_canonical = _canonical_validation_is_400(request.url.path)
        response_status = 400 if is_canonical else 422
        problem = ProblemDetails(
            type=problem_type_uri("validation"),
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
            headers={"Cache-Control": "no-store"}
            if request.url.path.endswith("/report.pdf")
            else None,
        )

    @app.exception_handler(PublicQueryError)
    async def public_query_error(request: Request, exc: PublicQueryError) -> JSONResponse:
        response_status = 404 if exc.code == "RESOURCE_NOT_FOUND" else 400
        if exc.code == "QUERY_TOO_BROAD":
            logger.info("public_read_rejected", extra={"context": {"reason": "query_too_broad"}})
        problem = ProblemDetails(
            type=problem_type_uri(exc.code.lower().replace("_", "-")),
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
            headers={"Cache-Control": "no-store"}
            if request.url.path.endswith("/report.pdf")
            else None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        problem = ProblemDetails(
            type=problem_type_uri(f"http-{exc.status_code}"),
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
                else "SERVICE_UNAVAILABLE"
                if exc.status_code == 503
                else None
            ),
        )
        return JSONResponse(
            problem.model_dump(mode="json", exclude={"code"} if problem.code is None else set()),
            status_code=exc.status_code,
            media_type="application/problem+json",
            headers=(
                {**(exc.headers or {}), "Cache-Control": "no-store"}
                if request.url.path.endswith("/report.pdf")
                else exc.headers
            ),
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
    async def ready() -> ReadinessResponse:
        try:
            # Startup success is latched; later dependency outages are informational.
            # Neo4j is probed at chat time and fails closed per ADR 0007; do not
            # block App Platform deploys on a private Bolt probe that can hang.
            # Feedback idempotency is process-local; multi-worker topology fails closed.
            chat_wired = not config.knowledge_chat_enabled or knowledge_chat_service is not None
            if not feedback_topology_safe:
                raise HTTPException(
                    status_code=503,
                    detail=FEEDBACK_TOPOLOGY_UNSAFE_DETAIL,
                )
            if snowflake_monitor.startup_succeeded and chat_wired:
                return ReadinessResponse(
                    dependencies=ReadinessDependencies(snowflake=snowflake_monitor.dependency_state)
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="A required data service is unavailable"
            ) from exc
        raise HTTPException(status_code=503, detail="A required data service is unavailable")

    app.state.snowflake_monitor = snowflake_monitor

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

    review_config, review_hash = load_config()

    @app.get(
        "/v1/states/{state}/review",
        response_model=StateReview,
        tags=["states"],
        summary="Review a state's county evidence",
        description=(
            "Versioned county Review result. Current rules are disabled. "
            "Lineage gaps are separate. Public cache TTL is 60 seconds."
        ),
        responses=LEGACY_ERRORS,
    )
    def state_review(
        state: Annotated[
            str,
            Path(
                pattern=r"^[A-Z]{2}$",
                description=(
                    "Two-letter uppercase USPS state code, for example `CO`. "
                    "A malformed value returns 422. A state not in the current release "
                    "returns 404."
                ),
                examples=["CO"],
            ),
        ],
        response: Response,
        observation_context: Annotated[
            str | None,
            Query(
                max_length=80,
                description=(
                    "Optional label for the observation context you are reviewing. "
                    "Atlas echoes it in the response and does not use it to change the result, "
                    "which always reflects the current cumulative county status. "
                    "Use up to 80 characters; a longer value returns 422."
                ),
                examples=["Current cumulative county status"],
            ),
        ] = None,
        dataset_version: DatasetVersion = None,
    ) -> StateReview:
        started = time.perf_counter()
        snapshot = service.snapshot()
        if dataset_version is not None and dataset_version != snapshot.metadata.release_id:
            raise HTTPException(status_code=404, detail="Dataset release not found")
        if state not in {item.state for item in snapshot.counties}:
            raise HTTPException(status_code=404, detail="State not found in current release")
        response.headers["Cache-Control"] = "public, max-age=60, must-revalidate"
        result = aggregate_state(
            state,
            snapshot,
            [CountyEvidence(county=item) for item in snapshot.counties if item.state == state],
            review_config,
            review_hash,
            observation_context,
        )
        logger.info(
            "county_review_evaluated",
            extra={
                "context": {
                    "methodology_id": result.methodology_id,
                    "methodology_version": result.methodology_version,
                    "config_sha256": result.configuration_sha256,
                    "result_state": result.result_state,
                    "rules_enabled": sum(rule.enabled for rule in review_config.rules.values()),
                    "rules_disabled": sum(
                        not rule.enabled for rule in review_config.rules.values()
                    ),
                    "candidates": len(result.review_candidates),
                    "abstentions": result.coverage.abstained_counties,
                    "data_gaps": len(result.data_gaps),
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                }
            },
        )
        return result

    @app.get(
        "/v1/atlas/metadata",
        response_model=AtlasMetadata,
        tags=["atlas"],
        operation_id="metadata_v1_atlas_metadata_get",
        summary="Get release metadata",
        description=(
            "Release metadata, source freshness and methodology; dataset_version selects an "
            "existing release. Cached publicly for 300 seconds with ETag. ETag is "
            "informational here; this route does not implement conditional 304."
        ),
        responses=LEGACY_ERRORS,
    )
    def metadata(response: Response, dataset_version: DatasetVersion = None) -> AtlasMetadata:
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
        summary="Get county map geometry",
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
    def geometry(request: Request, dataset_version: DatasetVersion = None) -> Response:
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
        dataset_version: DatasetVersion = None,
    ) -> ScoreCollection:
        try:
            result = service.scores(score_settings, dataset_version)
            response.headers["Cache-Control"] = "public, max-age=300"
            response.headers["ETag"] = _etag(result.model_dump_json().encode())
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Dataset release not found") from exc

    @app.get(
        "/v1/atlas/map-values",
        response_model=MapValuesCollection,
        tags=["atlas"],
        operation_id="map_values_v1_atlas_map_values_get",
        summary="Get map values by county",
        description=(
            "Published incidence floor, social vulnerability percentile, uninsured percent, "
            "and rural-urban continuum code for every county in the current snapshot. "
            "Optional dataset_version selects an existing release. Public cache TTL 300 "
            "seconds with informational ETag; no conditional 304."
        ),
        responses=LEGACY_ERRORS,
    )
    def map_values(
        response: Response, dataset_version: DatasetVersion = None
    ) -> MapValuesCollection:
        try:
            result = service.map_values(dataset_version)
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
        summary="Get county detail and provenance",
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
        fips: CountyFipsPath,
        score_settings: Annotated[ScoreSettings, Depends(_score_settings)],
        dataset_version: DatasetVersion = None,
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
        "/v1/counties/{fips}/surveillance-situations",
        response_model=CountySurveillanceSituations,
        tags=["counties"],
        operation_id="county_surveillance_situations_get",
        summary="Get county surveillance situations",
        description=(
            "Get both surveillance-situation classifications for one county: the primary "
            "`four_state` model, which includes human surveillance, and the secondary "
            "`tick_pathogen` model, which excludes it.\n\n"
            "### What you get back\n"
            "- Reviewed labels and four membership probabilities per model. Each is between 0 "
            "and 1, and they sum to 1 within 1e-6.\n"
            "- Model and batch lineage, source vintages, and interpretation limits.\n"
            "- `Unknown` is an available class. `No records` does not establish absence.\n"
            "- Membership is not disease risk, incidence, pathogen presence, causation, or "
            "Tier 1 priority.\n\n"
            "A model variant can return HTTP 200 with `UNAVAILABLE` and `null` data, including "
            "when the release is incompatible. The API does not infer or fall back to another "
            "result. The response must not be cached.\n\n"
            "### Common problems\n"
            "| Response | Cause and what to do |\n"
            "| --- | --- |\n"
            "| `404` | The county is unknown. |\n"
            "| `422` | The FIPS code is malformed. |\n"
            "| `503` | A dependency failed, stored data is malformed, or the release changed "
            "during the request. Retry later. |"
        ),
        responses={
            **LEGACY_ERRORS,
            200: {
                "description": "Both model variants, including explicit publication availability."
            },
        },
    )
    def county_surveillance_situations(
        response: Response, fips: CountyFipsAsciiPath
    ) -> CountySurveillanceSituations:
        result = situations.county(fips)
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get(
        "/v1/counties/{fips}/tier1-surveillance-priority",
        response_model=Tier1CountyPriority,
        tags=["counties"],
        operation_id="county_tier1_surveillance_priority_get",
        summary="Get county surveillance review priority",
        description=(
            "Get the approved Tier 1 surveillance review priority for one county from the "
            "current batch.\n\n"
            "### How to read it\n"
            "- This is surveillance review priority, not Lyme disease risk, predicted incidence, "
            "probability, clinical risk, or diagnosis.\n"
            "- LOW is a scored tier. Evidence sufficiency is separate.\n"
            "- Percentile is relative to the scored batch and population.\n"
            "- `generated_at_utc` is the model batch time, not the request time.\n\n"
            "Responses can be cached for 60 seconds.\n\n"
            "### Common problems\n"
            "| Response | Cause and what to do |\n"
            "| --- | --- |\n"
            "| `404` | The county has no published result. |\n"
            "| `503` | The current view is unavailable or the stored result is malformed. "
            "Retry later. |"
        ),
        responses=LEGACY_ERRORS,
    )
    def county_tier1_priority(
        request: Request,
        response: Response,
        fips: CountyFipsPath,
    ) -> Tier1CountyPriority:
        timing = Tier1Timing()
        request.state.tier1_timing = timing
        with tier1_request_timing(timing):
            result = tier1_priority.county(fips)
        if result is None:
            raise HTTPException(status_code=404, detail="No current Tier 1 county result")
        response.headers["Cache-Control"] = "public, max-age=60"
        return result

    @app.get(
        "/v1/counties/{fips}/tier1-surveillance-priority/availability",
        response_model=Tier1CountyPriorityAvailability,
        tags=["counties"],
        operation_id="county_tier1_surveillance_priority_availability_get",
        summary="Check surveillance priority availability",
        description=(
            "Check whether a county has a published Tier 1 result, and get it when it does.\n\n"
            "### What you get back\n"
            "- `AVAILABLE` with the published result in `data`.\n"
            "- `UNAVAILABLE` with `reason` `NO_PUBLISHED_MODEL` and `data` set to `null`.\n"
            "- `geography_release_id` names the release used to validate the FIPS code.\n\n"
            "`NOT_ESTIMABLE` is a published result state. It is different from no published "
            "model result. The API never turns `null` into zero or creates model output. "
            "Responses can be cached for 60 seconds.\n\n"
            "### Common problems\n"
            "| Response | Cause and what to do |\n"
            "| --- | --- |\n"
            "| `404` | The county is outside the current geography release. |\n"
            "| `422` | The FIPS code is malformed. |\n"
            "| `503` | A dependency failed or stored data is malformed. Retry later. |"
        ),
        responses={
            **LEGACY_ERRORS,
            404: problem_response(
                404,
                "RESOURCE_NOT_FOUND",
                "County is not in the current geography release.",
                description=DESCRIPTION_404,
                type_name="resource-not-found",
                title="Invalid public query",
            ),
        },
    )
    def county_tier1_priority_availability(
        request: Request,
        response: Response,
        fips: CountyFipsPath,
    ) -> Tier1CountyPriorityAvailability:
        timing = Tier1Timing()
        request.state.tier1_timing = timing
        with tier1_request_timing(timing):
            with tier1_phase("geography_membership"):
                snapshot = service.snapshot()
                current_fips = {county.fips for county in snapshot.counties}
                if fips not in current_fips:
                    raise PublicQueryError(
                        "RESOURCE_NOT_FOUND", "County is not in the current geography release."
                    )
            result = tier1_priority.county(fips)
        response.headers["Cache-Control"] = "public, max-age=60"
        if result is None:
            return Tier1CountyPriorityAvailability(
                availability="UNAVAILABLE",
                geography_release_id=snapshot.metadata.release_id,
                reason="NO_PUBLISHED_MODEL",
                data=None,
            )
        return Tier1CountyPriorityAvailability(
            availability="AVAILABLE",
            geography_release_id=snapshot.metadata.release_id,
            reason=None,
            data=result,
        )

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
            400: problem_response(
                400,
                "QUERY_TOO_BROAD",
                EXAMPLE_QUERY_TOO_BROAD.detail,
                description=DESCRIPTION_400_REPORT,
                examples=(EXAMPLE_QUERY_TOO_BROAD,),
            ),
            413: REPORT_TOO_LARGE_RESPONSE,
            503: REPORT_UNAVAILABLE_RESPONSE,
        },
        operation_id="county_report_pdf_v1_counties__fips__report_pdf_get",
        summary="Download a county report",
        description=(
            "Download a PDF report for one county.\n\n"
            "### Templates\n"
            "| `template` | What it returns |\n"
            "| --- | --- |\n"
            "| `county-v3` (default) | The two-page County Data Brief for the published gap "
            "score and Explorer follow-up. It ignores score-setting overrides, so a What-if "
            "request cannot change the published score, response category, recommendations, "
            "or questions. |\n"
            "| `county-v1` | The immutable earlier county report. It still binds score "
            "settings. |\n"
            "| `county-v2` | An opt-in report for selected observations. It reads canonical "
            "evidence, lineage, value states, and limitations. |\n\n"
            "### Key parameters\n"
            "| Parameter | What to send |\n"
            "| --- | --- |\n"
            "| `dataset_version` | Optional release to report on. `county-v2` requires it. |\n"
            "| `period_start`, `period_end`, `measure_id` | `county-v2` only. Send both dates "
            "and 1 to 20 measure IDs, repeated or comma-separated. `county-v1` and `county-v3` "
            "reject them with 422. |\n\n"
            "### Caching\n"
            "`ETag` and `If-None-Match` support 304. Published briefs can be cached for 300 "
            "seconds with revalidation. `county-v2` responses must not be cached, and the API "
            "revalidates authoritative context before it answers 304.\n\n"
            "### Common problems\n"
            "| Response | Cause and what to do |\n"
            "| --- | --- |\n"
            "| `404` | A `county-v2` observation is unknown. |\n"
            "| `413` | The report exceeds the resource limits. Narrow the selection. |\n"
            "| `422` | The template is unknown, or observation selectors were sent to "
            "`county-v1` or `county-v3`. |\n"
            "| `503` | `county-v2` provenance is missing or mismatched, or the renderer "
            "failed or timed out. Retry later. |"
        ),
    )
    def county_report_pdf(
        request: Request,
        fips: CountyFipsPath,
        score_settings: Annotated[ScoreSettings, Depends(_score_settings)],
        dataset_version: DatasetVersion = None,
        period_start: Annotated[
            date | None, Query(description="Exact inclusive observation start for county-v2.")
        ] = None,
        period_end: Annotated[
            date | None, Query(description="Exact inclusive observation end for county-v2.")
        ] = None,
        measure_id: Annotated[
            list[str] | None,
            Query(
                description="1–20 measure IDs; repeated or comma-separated for county-v2.",
                max_length=20,
            ),
        ] = None,
        template: Annotated[
            str,
            Query(
                pattern=r"^[a-z]+-v\d+$",
                description=(
                    "County report template. Accepted values are `county-v3` (default; "
                    "the two-page County Data Brief, which ignores score settings), "
                    "`county-v1`, and `county-v2` (requires `dataset_version`, "
                    "`period_start`, `period_end`, and `measure_id`). "
                    "A well-formed value that is not accepted returns 422."
                ),
                examples=["county-v3"],
            ),
        ] = "county-v3",
    ) -> Response:
        template_definition = TEMPLATE_REGISTRY.get(template)
        if template_definition is None or template_definition.geography_level != "county":
            raise HTTPException(
                status_code=422, detail="The requested county report template is not available."
            )
        # Web Orval fetch codegen serializes arrays as comma-separated values.
        if measure_id is not None:
            measure_id = [item for value in measure_id for item in value.split(",")]
        context_requested = any(x is not None for x in (period_start, period_end, measure_id))
        if template in {"county-v1", "county-v3"} and context_requested:
            raise HTTPException(status_code=422, detail="Observation context requires county-v2.")
        if template == "county-v2" and (
            not dataset_version
            or period_start is None
            or period_end is None
            or period_end < period_start
            or not measure_id
            or len(measure_id) > 20
            or len(set(measure_id)) != len(measure_id)
            or any(not x.strip() for x in measure_id)
        ):
            raise HTTPException(
                status_code=422,
                detail="county-v2 requires release, exact period and distinct measures.",
            )
        try:
            report = reports.county_report(fips, score_settings, dataset_version, template)
        except (KeyError, LookupError) as exc:
            raise HTTPException(
                status_code=404, detail="County or dataset release not found"
            ) from exc
        if template == "county-v2":
            assert period_start is not None and period_end is not None and measure_id
            report = reports.investigate_context(
                report, request.app.state.observation_service, measure_id, period_start, period_end
            )
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

        filename = (
            _atlas_brief_filename(report)
            if template == "county-v3"
            else "-".join(
                (
                    "lyme-gap-atlas",
                    _filename_component(report.geography.state_code),
                    _filename_component(report.geography.name),
                    _filename_component(report.geography.identifier),
                    _filename_component(report.provenance.dataset_version),
                )
            )
        )
        etag = _etag(payload)
        if _matches_etag(request, etag):
            return Response(
                status_code=304,
                headers={
                    "Cache-Control": (
                        "no-store"
                        if template == "county-v2"
                        else "public, max-age=300, must-revalidate"
                    ),
                    "ETag": etag,
                },
            )
        return Response(
            payload,
            media_type="application/pdf",
            headers={
                "Cache-Control": (
                    "no-store"
                    if template == "county-v2"
                    else "public, max-age=300, must-revalidate"
                ),
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
            413: REPORT_TOO_LARGE_RESPONSE,
            503: REPORT_UNAVAILABLE_RESPONSE,
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
        state: Annotated[
            str,
            Path(
                pattern=r"^[A-Z]{2}$",
                description=(
                    "Two-letter uppercase USPS state code, for example `CO`. "
                    "A malformed value returns 422. A state not in the current release "
                    "returns 404."
                ),
                examples=["CO"],
            ),
        ],
        score_settings: Annotated[ScoreSettings, Depends(_score_settings)],
        dataset_version: DatasetVersion = None,
        template: Annotated[
            str,
            Query(
                pattern=r"^[a-z]+-v\d+$",
                description=(
                    "State report template. The only accepted value is `state-v1`, "
                    "which is also the default. Any other value returns 422."
                ),
                examples=["state-v1"],
            ),
        ] = "state-v1",
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
        summary="Download county rankings as CSV",
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
        state: Annotated[
            str,
            Query(
                pattern=r"^(ALL|[A-Z]{2})$",
                description=(
                    "Limit rows to one state. Use a two-letter uppercase USPS state code, "
                    "or `ALL` for every state. The default is `ALL`. "
                    "Any other value returns 422."
                ),
                examples=["CO"],
            ),
        ] = "ALL",
        q: Annotated[
            str,
            Query(
                max_length=100,
                description=(
                    "Filter rows by text. Matching ignores case and keeps rows where the "
                    "county name, state code, or county FIPS code contains the text. "
                    "Use up to 100 characters; a longer value returns 422. "
                    "The default is empty, which keeps every row."
                ),
                examples=["boulder"],
            ),
        ] = "",
        evidence: Annotated[
            Literal["all", "ecological", "human", "complete"],
            Query(
                description=(
                    "Limit rows by available evidence. `all` keeps every county. "
                    "`ecological` keeps counties with a tick or pathogen signal. "
                    "`human` keeps counties with a published human case count. "
                    "`complete` keeps counties whose `evidence_completeness` is 5 or more. "
                    "The default is `all`. Any other value returns 422."
                ),
                examples=["all"],
            ),
        ] = "all",
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
        diagnostics: dict[str, Any] = {}
        result = StructuredAssistant(tools).ask(payload, diagnostics)
        diagnostics["outcome"] = result.answer.outcome.casefold()
        request.state.knowledge_chat_diagnostics = diagnostics
        return result

    @app.post(
        "/v1/assistant/mixed",
        response_model=MixedAssistantResponse,
        operation_id="askAtlasMixed",
        summary="Ask Atlas across governed evidence classes",
        description=(
            "Internal additive mixed-evidence branch bundle. The Structured branch uses "
            "bounded Atlas tools; the Literature branch uses the existing governed "
            "Research Assistant. Branch claims and citations retain their original "
            "identities. Cross-source comparison remains insufficient when material "
            "scope cannot be verified. No arbitrary query is accepted."
        ),
        tags=["assistant"],
        responses={
            429: {"model": ProblemDetails},
            503: {
                "model": MixedAssistantResponse,
                "headers": {"Retry-After": {"schema": {"type": "string"}}},
            },
        },
    )
    def ask_atlas_mixed(
        request: Request, payload: MixedAssistantRequest, response: Response
    ) -> MixedAssistantResponse:
        tools = StructuredTools(
            request.app.state.metadata_service,
            request.app.state.observation_service,
            request.app.state.provenance_service,
        )
        client = request.headers.get("do-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        request.state.knowledge_chat_diagnostics = {}
        result = MixedAssistant(
            StructuredAssistant(tools),
            knowledge_chat_service if config.knowledge_chat_enabled else None,
        ).ask(
            payload.question,
            payload.source_mode,
            payload.context,
            request.state.request_id,
            client,
            request.state.knowledge_chat_diagnostics,
        )
        diagnostics = request.state.knowledge_chat_diagnostics
        diagnostics["service_outcome"] = diagnostics.get("outcome")
        diagnostics["outcome"] = result.outcome.casefold()
        if result.outcome == "SOURCE_UNAVAILABLE":
            response.status_code = 503
            response.headers["Retry-After"] = "30"
        return MixedAssistantResponse.from_composition(result)

    @app.post(
        "/v1/knowledge-graph/chat",
        response_model=KnowledgeChatResponse,
        tags=["knowledge graph"],
        responses={
            200: {
                "description": (
                    "Typed outcome. `status` is answered, no_evidence, safety_refusal, "
                    "evidence_unavailable or capacity_limited. Retryable outcomes also "
                    "carry Retry-After. They are not 5xx because the DigitalOcean edge "
                    "replaces an origin 503 with its own 504 page and drops this body."
                ),
                "headers": {"Retry-After": {"schema": {"type": "string"}}},
            },
            429: {"model": ProblemDetails},
        },
    )
    def knowledge_graph_chat(
        request: Request, payload: KnowledgeChatRequest, response: Response
    ) -> KnowledgeChatResponse:
        if not config.knowledge_chat_enabled or knowledge_chat_service is None:
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
