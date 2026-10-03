"""Canonical public V1 REST adapter over governed read services."""

from datetime import date
from typing import Annotated, Any, NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from .config import ApiSettings
from .environmental_context import MEASURES
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
from .public_docs import (
    COLLECTION_DESCRIPTION,
    DETAIL_DESCRIPTION,
    EMPTY_COLLECTION_EXAMPLE,
    MISSING_OBSERVATION_EXAMPLE,
    problem_response,
)
from .public_metadata import MetadataService
from .public_observations import ObservationService
from .public_provenance import ProvenanceService

router = APIRouter()

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
    413: {
        "description": "GET request body rejected; application/problem+json",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    414: {
        "description": "Query string too long; application/problem+json",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
    503: {
        "description": "Canonical data service unavailable; application/problem+json",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    },
}

for _status, _code in {
    400: "INVALID_REQUEST",
    404: "RESOURCE_NOT_FOUND",
    429: "RATE_LIMITED",
    413: "INVALID_REQUEST",
    414: "INVALID_REQUEST",
    503: None,
}.items():
    _documented = problem_response(_status, _code, str(PROBLEMS[_status]["description"]))
    PROBLEMS[_status]["content"] = _documented["content"]
    if "headers" in _documented:
        PROBLEMS[_status]["headers"] = _documented["headers"]
PROBLEMS[200] = {"description": "Successful current-release resource."}


DETAIL_RESPONSES = {
    **PROBLEMS,
    304: {"description": "Not modified. ETag matches If-None-Match; empty response body."},
}


def pending() -> NoReturn:
    raise HTTPException(status_code=503, detail="Canonical data delivery is not yet available.")


def collection_pagination(
    request: Request,
    page_size: Annotated[
        int | None,
        Query(
            ge=1,
            le=500,
            description="Items per page; defaults to 100, configured maximum at most 500.",
            examples=[100],
        ),
    ] = None,
    page_token: Annotated[
        str | None,
        Query(description="Opaque next_page_token; retain query filters and release."),
    ] = None,
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


@router.get(
    "/v1/indicators",
    response_model=CollectionEnvelope[Indicator],
    responses=PROBLEMS,
    summary="Discover indicators",
    operation_id="indicators_v1_indicators_get",
    tags=["discovery"],
    description=COLLECTION_DESCRIPTION,
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
    "/v1/indicators/{indicator_id}",
    response_model=ResourceEnvelope[Indicator],
    responses=DETAIL_RESPONSES,
    operation_id="indicator_v1_indicators__indicator_id__get",
    tags=["discovery"],
    summary="Get an indicator",
    description=DETAIL_DESCRIPTION,
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


@router.get(
    "/v1/measures",
    response_model=CollectionEnvelope[Measure],
    responses=PROBLEMS,
    operation_id="measures_v1_measures_get",
    tags=["discovery"],
    summary="Discover measures",
    description=COLLECTION_DESCRIPTION,
)
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
    "/v1/measures/{measure_id}",
    response_model=ResourceEnvelope[Measure],
    responses=DETAIL_RESPONSES,
    operation_id="measure_v1_measures__measure_id__get",
    tags=["discovery"],
    summary="Get a measure",
    description=DETAIL_DESCRIPTION,
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


def provenance_service(request: Request) -> ProvenanceService:
    return cast(ProvenanceService, request.app.state.provenance_service)


@router.get(
    "/v1/sources",
    response_model=CollectionEnvelope[Source],
    responses=PROBLEMS,
    operation_id="sources_v1_sources_get",
    tags=["provenance"],
    summary="Discover sources",
    description=COLLECTION_DESCRIPTION,
)
def sources(
    request: Request,
    response: Response,
    pagination: Annotated[tuple[int, str | None], Depends(collection_pagination)],
    service: Annotated[ProvenanceService, Depends(provenance_service)],
) -> CollectionEnvelope[Source]:
    metadata_query(request, {"page_size", "page_token"})
    metadata_cache(response)
    page, token = service.sources(*pagination)
    return CollectionEnvelope(
        data=page, meta=CollectionMeta(next_page_token=token), links=CollectionLinks()
    )


@router.get(
    "/v1/sources/{source_id}",
    response_model=ResourceEnvelope[Source],
    responses=DETAIL_RESPONSES,
    operation_id="source_v1_sources__source_id__get",
    tags=["provenance"],
    summary="Get a source",
    description=DETAIL_DESCRIPTION,
)
def source(
    source_id: str,
    response: Response,
    service: Annotated[ProvenanceService, Depends(provenance_service)],
) -> ResourceEnvelope[Source]:
    metadata_cache(response)
    return ResourceEnvelope(data=service.source(source_id))


@router.get(
    "/v1/methodologies/{methodology_id}",
    response_model=ResourceEnvelope[Methodology],
    responses=DETAIL_RESPONSES,
    operation_id="methodology_v1_methodologies__methodology_id__get",
    tags=["provenance"],
    summary="Get a methodology",
    description=DETAIL_DESCRIPTION,
)
def methodology(
    methodology_id: str,
    response: Response,
    service: Annotated[ProvenanceService, Depends(provenance_service)],
) -> ResourceEnvelope[Methodology]:
    metadata_cache(response)
    return ResourceEnvelope(data=service.methodology(methodology_id))


@router.get(
    "/v1/geographies/{geography_type}/{geography_id}",
    response_model=ResourceEnvelope[Geography],
    responses=PROBLEMS,
    operation_id="geography_v1_geographies__geography_type___geography_id__get",
    tags=["geographies"],
    summary="Get geography identity (delivery pending)",
    description=(
        "Validated county/state FIPS identity; currently returns 503 "
        "CANONICAL_DATA_UNAVAILABLE. No geometry is returned."
    ),
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
    measure_id: Annotated[
        str,
        Query(
            min_length=1,
            description="Exact discovery measure ID.",
            examples=["case_count_floor_2023"],
        ),
    ],
    geography_type: GeographyType,
    geography_id: Annotated[
        list[str],
        Query(
            min_length=1,
            max_length=500,
            description="Repeat for 1-500 county FIPS strings; preserve leading zeros.",
            examples=[["08001"]],
        ),
    ],
    year: Annotated[
        int | None,
        Query(
            ge=1900,
            le=2100,
            description="Year or complete date range, not both; current published year is 2023.",
            examples=[2023],
        ),
    ] = None,
    start_date: Annotated[
        date | None,
        Query(
            description="Inclusive period start; requires end_date and excludes year.",
            examples=["2023-01-01"],
        ),
    ] = None,
    end_date: Annotated[
        date | None,
        Query(
            description="Inclusive period end; requires start_date and excludes year.",
            examples=["2023-12-31"],
        ),
    ] = None,
    stratification: Annotated[
        list[str] | None,
        Query(description="Current release has no strata; unsupported selections fail explicitly."),
    ] = None,
    page_size: Annotated[
        int | None,
        Query(
            ge=1,
            le=500,
            description="Items per page; defaults to 100, configured maximum at most 500.",
            examples=[100],
        ),
    ] = None,
    page_token: Annotated[
        str | None,
        Query(description="Opaque next_page_token; retain query filters and release."),
    ] = None,
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
    if query.geography_type != GeographyType.county:
        raise PublicQueryError("UNSUPPORTED_FILTER", "Only county geography is supported.")
    query.validate_bounds(
        ceiling=config.public_query_result_ceiling, annual=query.measure_id not in MEASURES
    )
    return query


def observation_service(request: Request) -> ObservationService:
    return cast(ObservationService, request.app.state.observation_service)


@router.get(
    "/v1/observations",
    response_model=CollectionEnvelope[Observation],
    responses={
        **PROBLEMS,
        200: {
            "description": "Bounded current-release observations.",
            "content": {
                "application/json": {
                    "examples": {
                        "empty": EMPTY_COLLECTION_EXAMPLE,
                        "missing": MISSING_OBSERVATION_EXAMPLE,
                    }
                }
            },
        },
    },
    description="Current published county observations only. Ordered by measure ID, county FIPS, "
    "period start, and observation ID. Opaque page tokens bind to filters and release. "
    "Annual surveillance uses 2023. Separately enabled, reviewed January 2025 environmental "
    'measures use labeled daily periods, carry environmental_context coverage, and have no '
    'supported strata. '
    "Environmental context is descriptive weather, with no causal, disease-risk or ML claim. "
    "A measure and bounded county/time selection are required; no user sorting or aggregation.",
    operation_id="observations_v1_observations_get",
    tags=["observations"],
    summary="Query county observations",
)
def observations(
    query: Annotated[ObservationQuery, Depends(observation_query)],
    service: Annotated[ObservationService, Depends(observation_service)],
) -> CollectionEnvelope[Observation]:
    return service.search(query)
