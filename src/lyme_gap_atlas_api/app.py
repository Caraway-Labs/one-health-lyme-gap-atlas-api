"""FastAPI application factory and versioned REST contract."""

import hashlib
import json
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from lyme_gap_atlas_shared import ScoreSettings
from lyme_gap_atlas_shared.observability import configure_logging, configure_tracing
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import ApiSettings, get_settings
from .middleware import RateLimitMiddleware, RequestContextMiddleware
from .models import AtlasMetadata, CountyDetail, ProblemDetails, ScoreCollection
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


def create_app(
    repository: AtlasRepository | None = None,
    settings: ApiSettings | None = None,
) -> FastAPI:
    config = settings or get_settings()
    configure_logging()
    configure_tracing("one-health-lyme-gap-atlas-api")
    service = AtlasService(repository or SnowflakeAtlasRepository(config), config.cache_ttl_seconds)
    app = FastAPI(
        title=config.app_name,
        version=config.app_version,
        description="Public read-only API for the One Health Lyme Gap Atlas.",
    )
    app.state.service = service
    app.add_middleware(GZipMiddleware, minimum_size=1_000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["Accept", "If-None-Match", "X-Request-ID"],
        expose_headers=["ETag", "X-Request-ID"],
    )
    app.add_middleware(RateLimitMiddleware, requests_per_minute=config.rate_limit_per_minute)
    app.add_middleware(RequestContextMiddleware)

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
            if service.ready():
                return {"status": "ready"}
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Snowflake is unavailable") from exc
        raise HTTPException(status_code=503, detail="Snowflake is unavailable")

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

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live")
    return app


app = create_app()
