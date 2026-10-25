"""Framework-native documentation metadata; no request or data behavior."""

import json
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_SUMMARY = "Governed Atlas analytics, metadata, and provenance"
API_DESCRIPTION = """Read-only JSON API for county and state Lyme disease surveillance,
public-health context, and the sources behind them. It is for developers, analysts, and
public-health practitioners. The base URL is
`https://api.onehealthatlas.org`, and public reads need no API key.

## Quickstart
Get the 2023 case-count floor for New York County (FIPS `36061`):

```bash
curl 'https://api.onehealthatlas.org/v1/observations?measure_id=case_count_floor_2023&geography_type=county&geography_id=36061&year=2023'
```

The response is trimmed to the fields that matter. The values are an example:

```json
{
  "data": [{
    "measure_id": "case_count_floor_2023",
    "geography": {"geography_type": "county", "geography_id": "36061"},
    "value": 1114.0, "value_state": "OBSERVED", "unit": "cases",
    "source_label": "CDC Lyme surveillance", "release_id": "...",
    "limitations": ["..."]
  }],
  "meta": {"next_page_token": null, "availability": "AVAILABLE"},
  "links": {"self": null}
}
```

## Responses
Collections return `data` (the items), `meta` (paging, `availability`, `release_id`,
response time), and `links`. A single resource returns `data`.

## Pagination
`page_size` defaults to 100 (maximum 500). Send `meta.next_page_token` as `page_token`,
unchanged and with the same filters. A `null` token means there are no more pages.

## Errors
Errors use RFC 9457 `application/problem+json` with a stable `code` and a `request_id`
to quote when you ask for support. A query that selects more than 10,000 results is
rejected with 400 `QUERY_TOO_BROAD`, not truncated.

## Response headers and caching
Every response carries `Content-Type` and `X-Request-ID`. Quote the request ID when you
ask for support. Each `200` response lists the other headers it sends, including
`Cache-Control` and `ETag`. Cache lifetimes differ by operation: 60 seconds for metadata
lists and details, 300 seconds for scores, map values, county detail, and reports, and one
year for display geometry. Observation queries set no cache lifetime.

Detail resources, display geometry, and PDF reports return a validator `ETag`. Send it back as
`If-None-Match` to get an empty `304` when nothing changed. Other operations ignore
`If-None-Match` and always return the full body. While the API serves a fallback copy of its
data, cacheable responses switch to `Cache-Control: private, no-store`.

## Rate limits and retries
Public reads need no key. Anonymous reads of the metadata, provenance, observation, and
geography availability operations are limited per client address to 60 requests in a rolling
minute and 5 requests at once. All other operations share a separate, higher per-address
limit. These figures are the current defaults and can change. Atlas publishes no per-key
quota or service level, so rely on the response headers, not on a fixed number.

A `429` response means you went over a limit. A `503` response means the data service or report
renderer cannot answer right now. Both can carry `Retry-After`, in whole seconds. Wait that
long before you retry. When the header is absent, wait 1 second, then double the wait
after each failure up to 60 seconds, and add a small random delay. Stop after a few attempts.
Do not retry a `400`, `404`, or `422`, because the same request fails again.

## Interpreting results
Read each value with its `value_state`, source, methodology version, `release_id`, and
`limitations`. Atlas results are not a diagnosis. Atlas results are not an estimate of
an individual's risk. Surveillance floors are minimum counts, not complete incidence.
Null, zero, suppressed, and no linked record mean different things. The response time
shows when Atlas built the response. It does not show when the publisher last updated
the data.

## Stability
`/v1` is the contract version, and the application version is separate. Optional fields
may be added within V1. Breaking changes follow a published deprecation plan.
"""

TAGS = [
    {
        "name": "discovery",
        "x-displayName": "Indicators and measures",
        "description": "List the indicators and measures you can query.",
    },
    {
        "name": "observations",
        "x-displayName": "Observations",
        "description": "Query measure values for counties, each with an explicit `value_state`.",
    },
    {
        "name": "provenance",
        "x-displayName": "Sources and methods",
        "description": "Look up the sources and methodologies behind a value, with versions.",
    },
    {
        "name": "geographies",
        "x-displayName": "Geographies",
        "description": (
            "Validate a county or state FIPS code. Atlas does not yet publish place records, "
            "so these operations return HTTP 200 with `availability` set to `UNAVAILABLE`."
        ),
    },
    {
        "name": "atlas",
        "x-displayName": "Scores and metadata",
        "description": "Get release metadata, scores, rankings, and map geometry.",
    },
    {
        "name": "counties",
        "x-displayName": "Counties",
        "description": "Get county detail and download county PDF reports.",
    },
    {"name": "states", "x-displayName": "States", "description": "Download state PDF reports."},
]

TAG_GROUPS = [
    {
        "name": "Atlas evidence (V1 resources)",
        "tags": ["discovery", "observations", "provenance", "geographies"],
    },
    {
        "name": "Atlas analytics and reports",
        "tags": ["atlas", "counties", "states"],
    },
]

COLLECTION_DESCRIPTION = (
    "Current-release discovery with optional documented filters. "
    "page_size defaults to 100 (maximum 500); reuse opaque page_token with "
    "the same filters and release. A null next_page_token ends traversal. "
    "Cache-Control: public, max-age=60, must-revalidate."
)
DETAIL_DESCRIPTION = (
    "Resolve the exact governed public resource ID in the current release. "
    "Unknown IDs return 404 RESOURCE_NOT_FOUND. Preserve versions and limitations; "
    "null metadata is not inferred. Cache-Control: public, max-age=60, must-revalidate; "
    "ETag with matching If-None-Match returns an empty 304."
)

PROBLEM_TYPE_BASE_URI = "https://onehealthatlas.org/problems"


def problem_type_uri(name: str) -> str:
    """Return the stable One Health Atlas URI for an RFC 9457 problem type."""
    return f"{PROBLEM_TYPE_BASE_URI}/{name}"


EXAMPLE_REQUEST_ID = "3f0a1c52-8d7e-4b6a-9c21-5e4d7a1b0f93"

STATUS_TITLES = {
    404: "Not found",
    429: "Too many requests",
    503: "Service unavailable",
}

CONTENT_TYPE_GUIDANCE = (
    "Check the `Content-Type` response header before you parse the body. "
    "Parse it as Problem Details only when it is `application/problem+json`."
)
RETRY_AFTER_DESCRIPTION = (
    "Seconds to wait before you retry. Wait that long when the header is present."
)

DESCRIPTION_400 = (
    "The request is invalid or unsupported. The `code` is `INVALID_REQUEST` for a missing, "
    "malformed, or inconsistent parameter, `UNSUPPORTED_FILTER` for a parameter V1 does not "
    "accept, `UNSUPPORTED_STRATIFICATION` for a stratification (observation queries only), or "
    "`QUERY_TOO_BROAD` when a query selects more than the result limit. "
    "Read `detail` and `errors`, change the request, then retry. "
    "The same request fails again."
)
DESCRIPTION_400_REPORT = (
    "The report's observation context selects more than the result limit. "
    "The `code` is `QUERY_TOO_BROAD`. Shorten the period or request fewer measures, then retry."
)
DESCRIPTION_404 = (
    "The resource ID, or the selection you asked for, is not in the current release. "
    "The `code` is `RESOURCE_NOT_FOUND` and `detail` names what is missing. "
    "Check the ID against the list operation for that resource. Retrying the same request "
    "returns the same response."
)
DESCRIPTION_404_LEGACY = (
    "The county, state, or dataset release you asked for is not in the current data. "
    "`detail` names what is missing. Check the ID or release, then retry with a value the API "
    "lists. Most of these responses have no `code`."
)
DESCRIPTION_413 = (
    "The request is a GET with a body. The `code` is `INVALID_REQUEST`. "
    "Send the request again without a body and put every value in the query string."
)
DESCRIPTION_413_REPORT = (
    "The report is larger than the API can render. Request a shorter period or fewer measures, "
    "then retry."
)
DESCRIPTION_414 = (
    "The query string is longer than the API accepts. The `code` is `INVALID_REQUEST`. "
    "Send fewer or shorter parameter values, then retry."
)
DESCRIPTION_422 = (
    "A request value failed validation. The body is Problem Details with an `errors` list that "
    "names each invalid input and where it was sent. It has no `code`. "
    "Fix each listed value, then retry. The same request fails again."
)
DESCRIPTION_429 = (
    "You sent too many requests in a short time. "
    "Wait the number of seconds in the `Retry-After` header, then retry. "
    "Spread requests out or reuse cached responses to stay under the limit."
)
DESCRIPTION_503 = (
    "The Atlas data service cannot answer right now. The `code` is `SERVICE_UNAVAILABLE`. "
    "Retry later with a growing delay, and wait the number of seconds in `Retry-After` "
    "when the header is present. " + CONTENT_TYPE_GUIDANCE
)
DESCRIPTION_503_REPORT = (
    "The report renderer is busy or timed out. Wait the number of seconds in `Retry-After`, "
    "then retry. " + CONTENT_TYPE_GUIDANCE
)


@dataclass(frozen=True)
class ProblemExample:
    """One named error body, copied from what the API returns."""

    name: str
    summary: str
    description: str
    detail: str
    code: str | None
    type_name: str
    title: str
    errors: list[dict[str, Any]] | None = None


EXAMPLE_INVALID_REQUEST = ProblemExample(
    name="invalid_request",
    summary="Missing required parameter",
    description="Shows `INVALID_REQUEST` when a required query parameter is absent.",
    detail="One or more request values are invalid.",
    code="INVALID_REQUEST",
    type_name="validation",
    title="Invalid request",
    errors=[
        {"type": "missing", "loc": ["query", "measure_id"], "msg": "Field required", "input": None}
    ],
)
EXAMPLE_UNSUPPORTED_FILTER = ProblemExample(
    name="unsupported_filter",
    summary="Query parameter V1 does not accept",
    description="Shows `UNSUPPORTED_FILTER` when a request includes a parameter outside V1.",
    detail="This query filter is not supported in V1.",
    code="UNSUPPORTED_FILTER",
    type_name="unsupported-filter",
    title="Invalid public query",
)
EXAMPLE_UNSUPPORTED_STRATIFICATION = ProblemExample(
    name="unsupported_stratification",
    summary="Stratification requested",
    description="Shows `UNSUPPORTED_STRATIFICATION` when a query sets `stratification`.",
    detail="This release has no governed strata.",
    code="UNSUPPORTED_STRATIFICATION",
    type_name="unsupported-stratification",
    title="Invalid public query",
)
EXAMPLE_QUERY_TOO_BROAD = ProblemExample(
    name="query_too_broad",
    summary="Selection above the result limit",
    description="Shows `QUERY_TOO_BROAD` when counties and period exceed the result limit.",
    detail="Narrow measure, geography, time, or stratification selection.",
    code="QUERY_TOO_BROAD",
    type_name="query-too-broad",
    title="Invalid public query",
)
EXAMPLE_RESOURCE_NOT_FOUND = ProblemExample(
    name="resource_not_found",
    summary="Unknown resource ID",
    description="Shows `RESOURCE_NOT_FOUND` for a selection that is not in the current release.",
    detail="Measure has no published county observations.",
    code="RESOURCE_NOT_FOUND",
    type_name="resource-not-found",
    title="Invalid public query",
)
EXAMPLE_RATE_LIMITED = ProblemExample(
    name="rate_limited",
    summary="Request limit reached",
    description="Shows `RATE_LIMITED`. The response also sets `Retry-After`.",
    detail="Public request limit reached.",
    code="RATE_LIMITED",
    type_name="rate-limited",
    title="Too many requests",
)
EXAMPLE_GET_BODY = ProblemExample(
    name="get_body_unsupported",
    summary="GET request with a body",
    description="Shows `INVALID_REQUEST` when a GET request carries a body.",
    detail="GET request bodies are unsupported.",
    code="INVALID_REQUEST",
    type_name="invalid-request",
    title="Payload too large",
)
EXAMPLE_QUERY_STRING_TOO_LONG = ProblemExample(
    name="query_string_too_long",
    summary="Query string over the length limit",
    description="Shows `INVALID_REQUEST` when the query string is too long.",
    detail="Query string is too long.",
    code="INVALID_REQUEST",
    type_name="invalid-request",
    title="URI too long",
)
EXAMPLE_SERVICE_UNAVAILABLE = ProblemExample(
    name="service_unavailable",
    summary="Data service unavailable",
    description="Shows `SERVICE_UNAVAILABLE` when the data service cannot answer.",
    detail="The governed Atlas data service is temporarily unavailable.",
    code="SERVICE_UNAVAILABLE",
    type_name="http-503",
    title="Service unavailable",
)
EXAMPLE_VALIDATION_422 = ProblemExample(
    name="validation_failed",
    summary="Path value has the wrong form",
    description="Shows HTTP 422 with an `errors` entry and no `code`.",
    detail="One or more request values are invalid.",
    code=None,
    type_name="validation",
    title="Invalid request",
    errors=[
        {
            "type": "string_pattern_mismatch",
            "loc": ["path", "fips"],
            "msg": "String should match pattern '^\\d{5}$'",
            "input": "abc",
            "ctx": {"pattern": "^\\d{5}$"},
        }
    ],
)
EXAMPLE_LEGACY_404 = ProblemExample(
    name="not_found",
    summary="Release not found",
    description="Shows HTTP 404 for a dataset release that is not current. It has no `code`.",
    detail="Dataset release not found",
    code=None,
    type_name="http-404",
    title="Not found",
)
EXAMPLE_LEGACY_429 = ProblemExample(
    name="rate_limited",
    summary="General request limit reached",
    description="Shows HTTP 429 with no `code`. The response also sets `Retry-After`.",
    detail="Request limit exceeded.",
    code=None,
    type_name="about:blank",
    title="Too Many Requests",
)

PUBLIC_400_EXAMPLES = (
    EXAMPLE_INVALID_REQUEST,
    EXAMPLE_UNSUPPORTED_FILTER,
    EXAMPLE_UNSUPPORTED_STRATIFICATION,
    EXAMPLE_QUERY_TOO_BROAD,
)

# Examples that apply to some operations only. Other operations drop them.
EXAMPLE_PATHS: dict[str, tuple[str, ...]] = {
    "unsupported_stratification": ("/v1/observations",),
    "query_too_broad": ("/v1/observations", "/v1/intelligence/"),
}

# Per-operation bodies, each confirmed against the code path that raises it.
EXAMPLE_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("/v1/indicators/{indicator_id}", "resource_not_found"): {
        "detail": "Indicator not found.",
        "title": "Not found",
        "type": problem_type_uri("http-404"),
    },
    ("/v1/measures/{measure_id}", "resource_not_found"): {
        "detail": "Measure not found.",
        "title": "Not found",
        "type": problem_type_uri("http-404"),
    },
    ("/v1/sources/{source_id}", "resource_not_found"): {"detail": "Source not found."},
    ("/v1/methodologies/{methodology_id}", "resource_not_found"): {
        "detail": "Methodology not found."
    },
    ("/v1/geographies/{geography_type}/{geography_id}", "resource_not_found"): {
        "detail": "County is not in the current geography release."
    },
    ("/v1/geographies/{geography_type}/{geography_id}/availability", "resource_not_found"): {
        "detail": "County is not in the current geography release."
    },
    ("/v1/counties/{fips}", "not_found"): {"detail": "County or dataset release not found"},
    ("/v1/counties/{fips}/surveillance-situations", "not_found"): {
        "detail": "County is not in the current geography release.",
        "title": "Invalid public query",
        "type": problem_type_uri("resource-not-found"),
        "code": "RESOURCE_NOT_FOUND",
    },
    ("/v1/counties/{fips}/report.pdf", "not_found"): {
        "detail": "County or dataset release not found"
    },
    ("/v1/counties/{fips}/tier1-surveillance-priority", "not_found"): {
        "detail": "No current Tier 1 county result"
    },
    ("/v1/states/{state}/review", "not_found"): {"detail": "State not found in current release"},
    ("/v1/states/{state}/report.pdf", "not_found"): {
        "detail": "State or dataset release not found"
    },
}


def _example_value(example: ProblemExample, status: int) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": example.type_name
        if example.type_name == "about:blank"
        else problem_type_uri(example.type_name),
        "title": example.title,
        "status": status,
        "detail": example.detail,
        "instance": "/v1/observations",
        "request_id": EXAMPLE_REQUEST_ID,
        "errors": example.errors,
    }
    if example.code is not None:
        value["code"] = example.code
    return value


def problem_response(
    status: int,
    code: str | None,
    detail: str,
    *,
    type_name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    examples: Sequence[ProblemExample] | None = None,
) -> dict[str, Any]:
    """Describe the RFC 9457 wire format with a description and named example bodies.

    ``description`` says when the status happens and what to do. Without ``examples``,
    one example is built from ``code``, ``detail``, ``type_name`` and ``title``.
    """
    if examples is None:
        examples = (
            ProblemExample(
                name=code.lower() if code else f"http_{status}",
                summary="Error response",
                description="Shows the body the API returns for this status.",
                detail=detail,
                code=code,
                type_name=type_name or f"http-{status}",
                title=title or STATUS_TITLES.get(status, "Request failed"),
            ),
        )
    result: dict[str, Any] = {
        "description": description or detail,
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetails"},
                "examples": {
                    example.name: {
                        "summary": example.summary,
                        "description": example.description,
                        "value": _example_value(example, status),
                    }
                    for example in examples
                },
            }
        },
    }
    if status in {429, 503}:
        result["headers"] = {
            "Retry-After": {
                "description": RETRY_AFTER_DESCRIPTION,
                "schema": {"type": "string"},
                "example": "30",
            }
        }
    return result


def route_problem_instances(schema: dict[str, Any]) -> None:
    """Set each RFC 9457 example's instance to its operation route template.

    Response maps are reused by FastAPI route declarations, so copy each
    response before editing to keep examples operation-specific. Examples that
    apply to other operations only are dropped, and bodies confirmed for one
    operation replace the shared default.
    """
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {})
            if not isinstance(responses, dict):
                continue
            for status, response in list(responses.items()):
                if not isinstance(response, dict):
                    continue
                content = response.get("content", {})
                problem = (
                    content.get("application/problem+json") if isinstance(content, dict) else None
                )
                if not isinstance(problem, dict):
                    continue
                response = deepcopy(response)
                responses[status] = response
                problem = response["content"]["application/problem+json"]
                examples = problem.get("examples", {})
                if isinstance(examples, dict):
                    for name in list(examples):
                        allowed = EXAMPLE_PATHS.get(name)
                        if (
                            allowed is not None
                            and len(examples) > 1
                            and not path.startswith(allowed)
                        ):
                            del examples[name]
                    for name, example in examples.items():
                        value = example.get("value") if isinstance(example, dict) else None
                        if isinstance(value, dict):
                            value["instance"] = path
                            value.update(EXAMPLE_OVERRIDES.get((path, name), {}))
                example = problem.get("example")
                if isinstance(example, dict):
                    example["instance"] = path


LEGACY_ERRORS: dict[int | str, dict[str, Any]] = {
    404: problem_response(
        404,
        None,
        EXAMPLE_LEGACY_404.detail,
        description=DESCRIPTION_404_LEGACY,
        examples=(EXAMPLE_LEGACY_404,),
    ),
    422: problem_response(
        422,
        None,
        EXAMPLE_VALIDATION_422.detail,
        description=DESCRIPTION_422,
        examples=(EXAMPLE_VALIDATION_422,),
    ),
    429: problem_response(
        429,
        None,
        EXAMPLE_LEGACY_429.detail,
        description=DESCRIPTION_429,
        examples=(EXAMPLE_LEGACY_429,),
    ),
    503: problem_response(
        503,
        "SERVICE_UNAVAILABLE",
        EXAMPLE_SERVICE_UNAVAILABLE.detail,
        description=DESCRIPTION_503,
        examples=(EXAMPLE_SERVICE_UNAVAILABLE,),
    ),
}

EXAMPLES_DIR = Path(__file__).parent / "openapi_examples"


def load_response_examples(directory: Path = EXAMPLES_DIR) -> dict[str, dict[str, Any]]:
    """Read committed response captures as ``{operationId: {media type: {name: example}}}``.

    Keys that start with an underscore hold capture notes and never reach the spec.
    """
    return {
        path.stem: {
            media_type: examples
            for media_type, examples in json.loads(path.read_text(encoding="utf-8")).items()
            if not media_type.startswith("_")
        }
        for path in sorted(directory.glob("*.json"))
    }


RESPONSE_EXAMPLES = load_response_examples()


def apply_response_examples(schema: dict[str, Any]) -> None:
    """Attach the captured named examples to each operation's `200` response.

    Examples are added after FastAPI serializes the spec because that step drops
    the `null` values that make missing and unavailable results meaningful.
    """
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            examples = RESPONSE_EXAMPLES.get(operation.get("operationId", ""))
            if not examples:
                continue
            content = operation["responses"]["200"]["content"]
            for media_type, named in examples.items():
                content[media_type]["examples"] = deepcopy(named)


REPORT_TOO_LARGE_RESPONSE = problem_response(
    413,
    None,
    "The report exceeds configured resource limits.",
    description=DESCRIPTION_413_REPORT,
    title="Request failed",
)
REPORT_UNAVAILABLE_RESPONSE = problem_response(
    503,
    "SERVICE_UNAVAILABLE",
    "Report rendering timed out. Please try again later.",
    description=DESCRIPTION_503_REPORT,
)


NO_STORE = "no-store"
CACHE_60 = "public, max-age=60, must-revalidate"
CACHE_300 = "public, max-age=300"
CACHE_300_REVALIDATE = "public, max-age=300, must-revalidate"
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"

FALLBACK_CACHE_NOTE = (
    " While the API serves a fallback copy of its data, it replaces a `public` value with "
    "`private, no-store`."
)
ETAG_VALUE_NOTE = " Send the value back exactly as you received it."


@dataclass(frozen=True)
class ResponseHeaders:
    """What one operation really sends on a `200`, and whether it answers `If-None-Match`."""

    cache: str | None = None
    cache_note: str | None = None
    etag: bool = False
    conditional: bool = False
    attachment: bool = False
    revalidated_304: bool = True


_DETAIL = ResponseHeaders(cache=CACHE_60, etag=True, conditional=True)
_COLLECTION = ResponseHeaders(cache=CACHE_60)
_TTL_300_ETAG = ResponseHeaders(cache=CACHE_300, etag=True)
_NO_STORE = ResponseHeaders(cache=NO_STORE)

# Each entry was confirmed against the route code and a live response.
RESPONSE_HEADERS: dict[str, ResponseHeaders] = {
    "indicators_v1_indicators_get": _COLLECTION,
    "measures_v1_measures_get": _COLLECTION,
    "sources_v1_sources_get": _COLLECTION,
    "indicator_v1_indicators__indicator_id__get": _DETAIL,
    "measure_v1_measures__measure_id__get": _DETAIL,
    "source_v1_sources__source_id__get": _DETAIL,
    "methodology_v1_methodologies__methodology_id__get": _DETAIL,
    "observations_v1_observations_get": ResponseHeaders(
        cache_note=(
            "The API sets no `Cache-Control` header on an available result, so do not assume "
            "a cache lifetime. It sends `no-store` when `meta.availability` is `UNAVAILABLE`."
        )
    ),
    "geography_v1_geographies__geography_type___geography_id__get": _NO_STORE,
    "canonicalGeographyAvailability": _NO_STORE,
    "county_surveillance_situations_get": _NO_STORE,
    "county_tier1_surveillance_priority_get": ResponseHeaders(cache="public, max-age=60"),
    "county_tier1_surveillance_priority_availability_get": ResponseHeaders(
        cache="public, max-age=60"
    ),
    "state_review_v1_states__state__review_get": _COLLECTION,
    "metadata_v1_atlas_metadata_get": _TTL_300_ETAG,
    "scores_v1_atlas_scores_get": _TTL_300_ETAG,
    "map_values_v1_atlas_map_values_get": _TTL_300_ETAG,
    "county_v1_counties__fips__get": _TTL_300_ETAG,
    "geometry_v1_atlas_geometry_get": ResponseHeaders(
        cache=CACHE_IMMUTABLE, etag=True, conditional=True, revalidated_304=False
    ),
    "county_report_pdf_v1_counties__fips__report_pdf_get": ResponseHeaders(
        cache=CACHE_300_REVALIDATE,
        cache_note=(
            "The `county-v1` and `county-v3` templates send this value. "
            "The `county-v2` template sends `no-store`."
        ),
        etag=True,
        conditional=True,
        attachment=True,
    ),
    "state_report_pdf_v1_states__state__report_pdf_get": ResponseHeaders(
        cache=CACHE_300_REVALIDATE, etag=True, conditional=True, attachment=True
    ),
    "ranking_csv_v1_atlas_ranking_csv_get": ResponseHeaders(
        attachment=True,
        cache_note="The API sets no `Cache-Control` header, so do not assume a cache lifetime.",
    ),
}

NOT_CONDITIONAL_SENTENCE = (
    " This operation ignores `If-None-Match` and always returns 200 with the full body."
)
REQUEST_ID_DESCRIPTION = (
    "Identifier for this request. Quote it when you ask for support. If you send a valid "
    "`X-Request-ID` request header, the API uses that value. Otherwise it generates one. "
    "A response that a shared cache replays can carry the identifier of the request that "
    "filled the cache."
)


def _success_headers(media_types: list[str], spec: ResponseHeaders) -> dict[str, Any]:
    headers: dict[str, Any] = {
        "Content-Type": {
            "description": "Media type of the body: "
            + ", ".join(f"`{m}`" for m in media_types)
            + ".",
            "schema": {"type": "string"},
            "example": media_types[0],
        },
    }
    if spec.cache is not None:
        description = f"How long a client or shared cache can reuse the response: `{spec.cache}`."
        if spec.cache_note:
            description += " " + spec.cache_note
        if spec.cache != NO_STORE:
            description += FALLBACK_CACHE_NOTE
        headers["Cache-Control"] = {
            "description": description,
            "schema": {"type": "string"},
            "example": spec.cache,
        }
    elif spec.cache_note:
        headers["Cache-Control"] = {
            "description": spec.cache_note,
            "schema": {"type": "string"},
        }
    if spec.etag:
        text = "Opaque validator for this representation."
        text += (
            " Send it as `If-None-Match` to get an empty 304 when nothing changed."
            + ETAG_VALUE_NOTE
            if spec.conditional
            else " It is informational here: the operation does not answer `If-None-Match`."
        )
        headers["ETag"] = {"description": text, "schema": {"type": "string"}}
    if spec.attachment:
        headers["Content-Disposition"] = {
            "description": "Marks the body as a download and suggests the file name.",
            "schema": {"type": "string"},
        }
    headers["X-Request-ID"] = {
        "description": REQUEST_ID_DESCRIPTION,
        "schema": {"type": "string"},
        "example": EXAMPLE_REQUEST_ID,
    }
    return headers


def apply_response_headers(schema: dict[str, Any]) -> None:
    """Document the headers each public `200` sends and how `If-None-Match` behaves.

    The table above lists only headers the code sets, so the spec never promises one the
    API does not send.
    """
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            spec = RESPONSE_HEADERS.get(operation.get("operationId", ""))
            if spec is None:
                continue
            responses = operation["responses"]
            ok = responses["200"]
            media_types = list(ok.get("content", {}))
            if ok.get("description") == "Successful Response":
                ok["description"] = "Successful response."
            ok["headers"] = _success_headers(media_types, spec)
            if not spec.conditional:
                ok["description"] = ok["description"].rstrip() + NOT_CONDITIONAL_SENTENCE
            not_modified = responses.get("304")
            if spec.conditional and not_modified is not None:
                not_modified["description"] = (
                    "Not modified. Your `If-None-Match` value matches the current `ETag`. "
                    "The response has no body."
                )
                if spec.revalidated_304:
                    not_modified["headers"] = {
                        "ETag": {
                            "description": "The matching validator.",
                            "schema": {"type": "string"},
                        },
                        "Cache-Control": {
                            "description": "The same cache rule as the `200` response.",
                            "schema": {"type": "string"},
                        },
                    }
