"""Contract-only canonical REST adapter; data-backed services arrive in #53-#55."""

from datetime import date
from typing import Annotated, Any, NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from .config import ApiSettings
from .public_contract import (
    CollectionEnvelope,
    CollectionLinks,
    CollectionMeta,
    Geography,
    GeographyType,
    Indicator,
    Measure,
    Methodology,
    Observation,
    ObservationQuery,
    PublicQueryError,
    ResourceEnvelope,
    Source,
)
from .public_metadata import MetadataService

router = APIRouter(tags=["public-v1"])

PROBLEMS: dict[int | str, dict[str, Any]] = {
    "4XX": {
        "description": "Canonical client errors use application/problem+json.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    400: {
        "description": "INVALID_REQUEST, UNSUPPORTED_FILTER, "
        "UNSUPPORTED_STRATIFICATION, or QUERY_TOO_BROAD; application/problem+json",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    404: {
        "description": "RESOURCE_NOT_FOUND; application/problem+json",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    429: {
        "description": "Rate limited; Retry-After; application/problem+json",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    503: {
        "description": "Canonical data service pending #53-#55; application/problem+json",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
}


def pending() -> NoReturn:
    raise HTTPException(status_code=503, detail="Canonical data delivery is not yet available.")


def collection_pagination(
    request: Request,
    page_size: Annotated[int | None, Query(ge=1, le=500)] = None,
    page_token: str | None = None,
) -> tuple[int, str | None]:
    config: ApiSettings = request.app.state.public_settings
    size = page_size or config.public_page_size_default
    if size > config.public_page_size_max:
        raise PublicQueryError("INVALID_REQUEST", "page_size exceeds configured maximum.")
    return size, page_token


def metadata_query(request: Request, allowed: set[str]) -> None:
    if set(request.query_params) - allowed:
        raise PublicQueryError("UNSUPPORTED_FILTER", "This query filter is not supported in V1.")


def metadata_service(request: Request) -> MetadataService:
    return cast(MetadataService, request.app.state.metadata_service)


def metadata_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "public, max-age=60, must-revalidate"


def pending_pagination(
    pagination: Annotated[tuple[int, str | None], Depends(collection_pagination)],
) -> None:
    if pagination[1]:
        raise PublicQueryError("INVALID_REQUEST", "No continuation token has been issued.")


@router.get(
    "/v1/indicators",
    response_model=CollectionEnvelope[Indicator],
    responses=PROBLEMS,
    summary="Discover indicators",
)
def indicators(
    request: Request,
    response: Response,
    pagination: Annotated[tuple[int, str | None], Depends(collection_pagination)],
    service: Annotated[MetadataService, Depends(metadata_service)],
    indicator_id: str | None = None,
) -> CollectionEnvelope[Indicator]:
    metadata_cache(response)
    metadata_query(request, {"page_size", "page_token", "indicator_id"})
    items, _ = service.discover()
    if indicator_id is not None:
        items = [item for item in items if item.indicator_id == indicator_id]
    page, token = service.page(items, *pagination, {"indicator_id": indicator_id})
    return CollectionEnvelope(
        data=page, meta=CollectionMeta(next_page_token=token), links=CollectionLinks()
    )


@router.get(
    "/v1/indicators/{indicator_id}", response_model=ResourceEnvelope[Indicator], responses=PROBLEMS
)
def indicator(
    indicator_id: str,
    response: Response,
    service: Annotated[MetadataService, Depends(metadata_service)],
) -> ResourceEnvelope[Indicator]:
    metadata_cache(response)
    items, _ = service.discover()
    for item in items:
        if item.indicator_id == indicator_id:
            return ResourceEnvelope(data=item)
    raise HTTPException(status_code=404, detail="Indicator not found.")


@router.get("/v1/measures", response_model=CollectionEnvelope[Measure], responses=PROBLEMS)
def measures(
    request: Request,
    response: Response,
    pagination: Annotated[tuple[int, str | None], Depends(collection_pagination)],
    service: Annotated[MetadataService, Depends(metadata_service)],
    measure_id: str | None = None,
    indicator_id: str | None = None,
    geography_type: str | None = None,
) -> CollectionEnvelope[Measure]:
    metadata_cache(response)
    metadata_query(
        request, {"page_size", "page_token", "measure_id", "indicator_id", "geography_type"}
    )
    _, items = service.discover()
    if measure_id is not None:
        items = [item for item in items if item.measure_id == measure_id]
    if indicator_id is not None:
        items = [item for item in items if item.indicator_id == indicator_id]
    if geography_type is not None:
        items = [item for item in items if item.geography_semantics == geography_type]
    query = {
        "measure_id": measure_id,
        "indicator_id": indicator_id,
        "geography_type": geography_type,
    }
    page, token = service.page(items, *pagination, query)
    return CollectionEnvelope(
        data=page, meta=CollectionMeta(next_page_token=token), links=CollectionLinks()
    )


@router.get(
    "/v1/measures/{measure_id}", response_model=ResourceEnvelope[Measure], responses=PROBLEMS
)
def measure(
    measure_id: str,
    response: Response,
    service: Annotated[MetadataService, Depends(metadata_service)],
) -> ResourceEnvelope[Measure]:
    metadata_cache(response)
    _, items = service.discover()
    for item in items:
        if item.measure_id == measure_id:
            return ResourceEnvelope(data=item)
    raise HTTPException(status_code=404, detail="Measure not found.")


@router.get("/v1/sources", response_model=CollectionEnvelope[Source], responses=PROBLEMS)
def sources(_pagination: Annotated[None, Depends(pending_pagination)]) -> NoReturn:
    pending()


@router.get("/v1/sources/{source_id}", response_model=ResourceEnvelope[Source], responses=PROBLEMS)
def source(source_id: str) -> NoReturn:
    pending()


@router.get(
    "/v1/methodologies/{methodology_id}",
    response_model=ResourceEnvelope[Methodology],
    responses=PROBLEMS,
)
def methodology(methodology_id: str) -> NoReturn:
    pending()


@router.get(
    "/v1/geographies/{geography_type}/{geography_id}",
    response_model=ResourceEnvelope[Geography],
    responses=PROBLEMS,
)
def geography(geography_type: GeographyType, geography_id: str) -> NoReturn:
    from .public_contract import GeographyIdentity

    try:
        GeographyIdentity(geography_type=geography_type, geography_id=geography_id)
    except ValueError as exc:
        raise PublicQueryError("INVALID_REQUEST", str(exc)) from exc
    pending()


def observation_query(
    request: Request,
    measure_id: Annotated[str, Query(min_length=1)],
    geography_type: GeographyType,
    geography_id: Annotated[list[str], Query(min_length=1, max_length=500)],
    year: Annotated[int | None, Query(ge=1900, le=2100)] = None,
    start_date: date | None = None,
    end_date: date | None = None,
    stratification: Annotated[list[str] | None, Query()] = None,
    page_size: Annotated[int | None, Query(ge=1, le=500)] = None,
    page_token: str | None = None,
) -> ObservationQuery:
    config: ApiSettings = request.app.state.public_settings
    allowed = {
        "measure_id",
        "geography_type",
        "geography_id",
        "year",
        "start_date",
        "end_date",
        "stratification",
        "page_size",
        "page_token",
    }
    if set(request.query_params) - allowed:
        raise PublicQueryError("UNSUPPORTED_FILTER", "This query filter is not supported in V1.")
    if page_size is None:
        page_size = config.public_page_size_default
    if page_size > config.public_page_size_max:
        raise PublicQueryError("INVALID_REQUEST", "page_size exceeds configured maximum.")
    query = ObservationQuery(
        measure_id=measure_id,
        geography_type=geography_type,
        geography_id=geography_id,
        year=year,
        start_date=start_date,
        end_date=end_date,
        stratification=stratification or [],
        page_size=page_size,
        page_token=page_token,
    )
    query.validate_bounds(ceiling=config.public_query_result_ceiling)
    if query.stratification:
        raise PublicQueryError(
            "UNSUPPORTED_STRATIFICATION",
            "Stratification must be declared by the measure; discovery is pending #53.",
        )
    if query.page_token:
        raise PublicQueryError(
            "INVALID_REQUEST", "No continuation token has been issued for this query."
        )
    return query


@router.get(
    "/v1/observations",
    response_model=CollectionEnvelope[Observation],
    responses=PROBLEMS,
    description="Ordered by measure, geography, period, strata, and evidence ID. "
    "Opaque page tokens bind to query and continuation state; no user sorting or aggregation. "
    "A measure and bounded typed geography/time selection are required.",
)
def observations(query: Annotated[ObservationQuery, Depends(observation_query)]) -> NoReturn:
    pending()
