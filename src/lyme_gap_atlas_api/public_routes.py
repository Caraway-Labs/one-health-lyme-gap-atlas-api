"""Canonical public V1 REST adapter over governed read services."""

from datetime import date
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response

from .climate_timing import ClimateTiming, climate_request_timing
from .config import ApiSettings
from .context_timing import ContextTiming, context_request_timing
from .county_context_ids import CONTEXT_FIELDS
from .environmental_context import MEASURES
from .geography_availability import (
    CanonicalGeographyAvailability,
    GeographyUnavailable,
    county_availability,
    geography_unavailable,
)
from .public_contract import (
    CollectionEnvelope,
    CollectionLinks,
    CollectionMeta,
    Geography,
    GeographyIdentity,
    GeographyType,
    Indicator,
    Measure,
    Methodology,
    ObservationCollection,
    ObservationQuery,
    PublicQueryError,
    ResourceEnvelope,
    Source,
)
from .public_docs import (
    COLLECTION_DESCRIPTION,
    DETAIL_DESCRIPTION,
    problem_response,
)
from .public_metadata import MetadataService
from .public_observations import ObservationService
from .public_provenance import ProvenanceService
from .repository import AtlasDataUnavailableError
from .service import AtlasService

router = APIRouter()

PROBLEMS: dict[int | str, dict[str, Any]] = {
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
        "description": "Service unavailable; application/problem+json",
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
    503: "SERVICE_UNAVAILABLE",
}.items():
    _documented = problem_response(_status, _code, str(PROBLEMS[_status]["description"]))
    PROBLEMS[_status]["content"] = _documented["content"]
    if "headers" in _documented:
        PROBLEMS[_status]["headers"] = _documented["headers"]
PROBLEMS[200] = {"description": "Successful current-release resource."}

OBSERVATIONS_DESCRIPTION = """Query published county observations for one measure and up to 500 \
counties.

### What it returns
Observations from the current release, ordered by measure ID, county FIPS, period start, then \
observation ID. You cannot sort or aggregate. Each observation carries its `value_state`, \
source, methodology, and limitations.

### Required parameters
Send `measure_id`, `geography_type=county`, one or more `geography_id` values, and the time \
parameter that the measure type accepts.

### Time parameters by measure type
| Measure type | Send | Wrong choice returns |
| --- | --- | --- |
| Annual surveillance (data is for 2023) | `year`, or `start_date` and `end_date` | 400 \
`INVALID_REQUEST` if you send both `year` and dates, or neither. 400 `UNSUPPORTED_FILTER` if \
you send `vintage_year`. |
| Daily environmental (January 2025 weather measures, enabled separately) | `start_date` and \
`end_date`, or `year` | 400 `INVALID_REQUEST` if you send only one date, or both `year` and \
dates. 400 `UNSUPPORTED_FILTER` if you send `vintage_year`. |
| CDC cumulative status (`scapularis_status`, `burgdorferi_status`) | `start_date=2025-12-31` \
and `end_date=2025-12-31` (grain `CUMULATIVE`) | 400 `UNSUPPORTED_FILTER` for `year` or any \
other date range. The API rejects it before it reads data. |
| County context vintages (SVI, population, insurance) | `vintage_year=2022` (periods are ACS \
2018-2022) | 400 `INVALID_REQUEST` if `vintage_year` is missing. 400 `UNSUPPORTED_FILTER` for \
another vintage, `year`, `start_date`, or `end_date`. |
| RUCC (`rucc_2023`) | `vintage_year=2023` (categorical codes and labels, with `null` period \
dates) | 400 `INVALID_REQUEST` if `vintage_year` is missing. 400 `UNSUPPORTED_FILTER` for \
another vintage, `year`, `start_date`, or `end_date`. |

### Value states
| Case | How to read it |
| --- | --- |
| Environmental measures | Descriptive weather with labeled daily periods and \
`environmental_context` coverage. They have no strata and make no causal, disease-risk, or \
machine-learning claim. |
| Surveillance context | Optional, and it needs a published delivery. `normalized_state` \
preserves the publisher's meaning, and the publisher's value stays in `value`. |
| Missing context | It does not establish absence or temporal comparability. |
| Absent county context rows | They are unavailable evidence, never zero. |

### Empty versus unavailable results
| Situation | Response |
| --- | --- |
| An initial query finds no published results (any measure except CDC status) | HTTP 200 with \
`meta.availability` `UNAVAILABLE` and `reason` `NO_PUBLISHED_RESULTS`. This does not establish \
source coverage or biological absence. |
| You page past the last result | HTTP 200 with `meta.availability` `AVAILABLE`. |
| CDC status is not published for the county, or a service error occurs | A non-2xx error \
response, not a 200. |

### Paging
Send `next_page_token` unchanged with the same filters. Page tokens are tied to the filters and \
the release.

### Caching
An `UNAVAILABLE` collection must not be cached.
"""

CANONICAL_GEOGRAPHY_PROBLEMS = {
    **{status: documented for status, documented in PROBLEMS.items() if status != 503},
    200: {
        "description": (
            "Typed answer. While canonical geography identity delivery is unpublished this is "
            "availability UNAVAILABLE with data null (HTTP 200, not 5xx, because the hosting "
            "edge replaces origin 5xx responses with an HTML 504)."
        )
    },
}


DETAIL_RESPONSES = {
    **PROBLEMS,
    304: {"description": "Not modified. ETag matches If-None-Match; empty response body."},
}


GeographyTypePath = Annotated[
    GeographyType,
    Path(
        description=(
            "Kind of place: `county` or `state`. Any other value returns 400 `INVALID_REQUEST`."
        ),
        examples=["county"],
    ),
]
GeographyIdPath = Annotated[
    str,
    Path(
        description=(
            "FIPS code of the place, sent as a string with leading zeros kept: "
            "five digits for a county, two for a state. "
            "A code of the wrong length or with non-digits returns 400 `INVALID_REQUEST`. "
            "Connecticut counties use planning-region codes such as `09110`."
        ),
        examples=["08001"],
    ),
]


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
    indicator_id: Annotated[
        str | None,
        Query(
            description=(
                "Return only the indicator with this exact ID. "
                "Omit it to list every indicator. An ID that matches nothing returns an empty list."
            ),
            examples=["human_disease_burden"],
        ),
    ] = None,
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
    indicator_id: Annotated[
        str,
        Path(
            description=("Exact indicator ID from the indicator list. An unknown ID returns 404."),
            examples=["human_disease_burden"],
        ),
    ],
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
    measure_id: Annotated[
        str | None,
        Query(
            description=(
                "Return only the measure with this exact ID. "
                "Omit it to list every measure. An ID that matches nothing returns an empty list."
            ),
            examples=["case_count_floor_2023"],
        ),
    ] = None,
    indicator_id: Annotated[
        str | None,
        Query(
            description=(
                "Return only measures that belong to this indicator ID. "
                "Omit it to list every measure."
            ),
            examples=["human_disease_burden"],
        ),
    ] = None,
    geography_type: Annotated[
        str | None,
        Query(
            description=(
                "Return only measures whose `geography_semantics` field equals this value "
                "exactly, for example `COUNTY_FIPS_5`. Omit it to list every measure."
            ),
            examples=["COUNTY_FIPS_5"],
        ),
    ] = None,
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
    measure_id: Annotated[
        str,
        Path(
            description="Exact measure ID from the measure list. An unknown ID returns 404.",
            examples=["case_count_floor_2023"],
        ),
    ],
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
    source_id: Annotated[
        str,
        Path(
            description=(
                "Exact source ID, as given in a `source_id` field. An unknown ID returns 404."
            ),
            examples=["human"],
        ),
    ],
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
    methodology_id: Annotated[
        str,
        Path(
            description=(
                "Exact methodology ID, as given in a `methodology_id` field. "
                "An unknown ID returns 404."
            ),
            examples=["human_confirmed_probable_case_floor_v1"],
        ),
    ],
    response: Response,
    service: Annotated[ProvenanceService, Depends(provenance_service)],
) -> ResourceEnvelope[Methodology]:
    metadata_cache(response)
    return ResourceEnvelope(data=service.methodology(methodology_id))


@router.get(
    "/v1/geographies/{geography_type}/{geography_id}",
    response_model=ResourceEnvelope[Geography] | GeographyUnavailable,
    responses=CANONICAL_GEOGRAPHY_PROBLEMS,
    operation_id="geography_v1_geographies__geography_type___geography_id__get",
    tags=["geographies"],
    summary="Get geography identity (delivery pending)",
    description=(
        "Validated county/state FIPS identity. While canonical identity delivery is "
        "unpublished the response is HTTP 200 with availability UNAVAILABLE, reason "
        "CANONICAL_GEOGRAPHY_UNPUBLISHED and data null; the identity is validated for shape "
        "only. No geometry is returned. Responses use Cache-Control: no-store."
    ),
)
def geography(
    geography_type: GeographyTypePath, geography_id: GeographyIdPath, response: Response
) -> GeographyUnavailable:
    from .public_contract import GeographyIdentity

    try:
        identity = GeographyIdentity(geography_type=geography_type, geography_id=geography_id)
    except ValueError as exc:
        raise PublicQueryError("INVALID_REQUEST", str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return geography_unavailable(identity)


@router.get(
    "/v1/geographies/{geography_type}/{geography_id}/availability",
    response_model=CanonicalGeographyAvailability,
    responses={
        **PROBLEMS,
        200: {
            "description": "Confirmed current county identity; canonical publication is pending."
        },
    },
    operation_id="canonicalGeographyAvailability",
    tags=["geographies"],
    summary="Check canonical county geography publication availability",
    description=(
        "Check whether a county has published canonical geography data.\n\n"
        "### When to use it\n"
        "The API confirms the county exists in the current release. It returns HTTP 200 with "
        "`availability` set to `UNAVAILABLE`, `reason` set to "
        "`CANONICAL_GEOGRAPHY_UNPUBLISHED`, and `data` set to `null`. The resource describes "
        "identity only, and the API returns no geometry. State availability is not supported.\n\n"
        "### Common problems\n"
        "| Response | Cause and what to do |\n"
        "| --- | --- |\n"
        "| `400` | The ID is malformed, or you asked for a state. Send a five-digit county "
        "FIPS code. |\n"
        "| `404` | The county is not in the current release. |\n"
        "| `503` | The county directory is unavailable, empty, duplicated, or inconsistent with "
        "the current release. Retry later. |\n\n"
        "The response must not be cached."
    ),
)
def geography_availability(
    request: Request,
    response: Response,
    geography_type: GeographyTypePath,
    geography_id: GeographyIdPath,
) -> CanonicalGeographyAvailability:
    try:
        GeographyIdentity(geography_type=geography_type, geography_id=geography_id)
    except ValueError as exc:
        raise PublicQueryError("INVALID_REQUEST", str(exc)) from exc
    if geography_type != GeographyType.county:
        raise PublicQueryError("UNSUPPORTED_FILTER", "Only county availability is supported.")
    service = cast(AtlasService, request.app.state.service)
    snapshot = service.snapshot()
    observations = cast(ObservationService, request.app.state.observation_service)
    if observations.repository.current_release() != snapshot.metadata.release_id:
        raise AtlasDataUnavailableError("Current geography release is unavailable")
    result = county_availability(snapshot, geography_id)
    response.headers["Cache-Control"] = "no-store"
    return result


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
    geography_type: Annotated[
        GeographyType,
        Query(
            description=(
                "Kind of place. Only `county` is supported; `state` returns 400 "
                "`UNSUPPORTED_FILTER`."
            ),
            examples=["county"],
        ),
    ],
    geography_id: Annotated[
        list[str],
        Query(
            min_length=1,
            max_length=500,
            description="Repeat for 1-500 county FIPS strings; preserve leading zeros.",
            examples=[["08001"]],
        ),
    ],
    vintage_year: Annotated[
        int | None,
        Query(
            ge=1900,
            le=2100,
            description="Required for county context: 2022 for SVI/ACS, 2023 for RUCC. "
            "Excludes year/date filters.",
        ),
    ] = None,
    year: Annotated[
        int | None,
        Query(
            ge=1900,
            le=2100,
            description="Annual year or complete date range, not both. CDC cumulative status "
            "rejects year; use start_date=end_date=2025-12-31.",
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
        "vintage_year",
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
        vintage_year=vintage_year,
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
    response_model=ObservationCollection,
    responses={
        **PROBLEMS,
        200: {"description": "Bounded current-release observations."},
    },
    description=OBSERVATIONS_DESCRIPTION,
    operation_id="observations_v1_observations_get",
    tags=["observations"],
    summary="Query county observations",
)
def observations(
    request: Request,
    response: Response,
    query: Annotated[ObservationQuery, Depends(observation_query)],
    service: Annotated[ObservationService, Depends(observation_service)],
) -> ObservationCollection:
    from .cdc_surveillance import IDENTITIES
    from .cdc_timing import CDCTiming, cdc_request_timing

    if query.measure_id in IDENTITIES and service.cdc_adapter is not None:
        cdc_timing = CDCTiming()
        request.state.cdc_timing = cdc_timing
        with cdc_request_timing(cdc_timing):
            result = service.search(query)
    elif query.measure_id in MEASURES:
        timing = ClimateTiming()
        request.state.climate_timing = timing
        with climate_request_timing(timing):
            result = service.search(query)
    elif query.measure_id in CONTEXT_FIELDS:
        context_timing = ContextTiming()
        request.state.context_timing = context_timing
        with context_request_timing(context_timing):
            result = service.search(query)
    else:
        result = service.search(query)
    if result.meta.availability == "UNAVAILABLE":
        response.headers["Cache-Control"] = "no-store"
    return result
