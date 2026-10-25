"""Framework-native documentation metadata; no request or data behavior."""

from copy import deepcopy
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

## Caching and retries
Metadata and observation responses can be cached for 60 seconds. Detail responses
return an `ETag`; send it as `If-None-Match` to get an empty 304 when nothing changed.
Wait for `Retry-After` seconds before you retry a 429 or 503.

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
        "description": "List the indicators and measures you can query.",
    },
    {
        "name": "observations",
        "description": "Query measure values for counties, each with an explicit `value_state`.",
    },
    {
        "name": "provenance",
        "description": "Look up the sources and methodologies behind a value, with versions.",
    },
    {
        "name": "geographies",
        "description": (
            "Validate a county or state FIPS code. Atlas does not yet publish place records, "
            "so these operations return HTTP 200 with `availability` set to `UNAVAILABLE`."
        ),
    },
    {
        "name": "atlas",
        "description": "Get release metadata, scores, rankings, and map geometry.",
    },
    {
        "name": "counties",
        "description": "Get county detail and download county PDF reports.",
    },
    {"name": "states", "description": "Download state PDF reports."},
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


def problem_response(
    status: int,
    code: str | None,
    detail: str,
    *,
    type_name: str | None = None,
    title: str = "Request failed",
) -> dict[str, Any]:
    """Describe the existing RFC 9457 wire format using a synthetic example."""
    value: dict[str, Any] = {
        "type": problem_type_uri(type_name or f"http-{status}"),
        "title": title,
        "status": status,
        "detail": detail,
        "instance": "/v1/observations",
        "request_id": "example-request-id",
        "errors": None,
    }
    if code is not None:
        value["code"] = code
    result: dict[str, Any] = {
        "description": detail,
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetails"},
                "examples": {
                    "illustrative": {"summary": "Synthetic error example", "value": value}
                },
            }
        },
    }
    if status in {429, 503}:
        result["headers"] = {
            "Retry-After": {
                "description": "Seconds before retry when supplied by the service.",
                "schema": {"type": "string"},
            }
        }
    return result


def route_problem_instances(schema: dict[str, Any]) -> None:
    """Set each RFC 9457 example's instance to its operation route template.

    Response maps are reused by FastAPI route declarations, so copy each
    response before editing to keep examples operation-specific.
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
                    for example in examples.values():
                        value = example.get("value") if isinstance(example, dict) else None
                        if isinstance(value, dict):
                            value["instance"] = path
                example = problem.get("example")
                if isinstance(example, dict):
                    example["instance"] = path


LEGACY_ERRORS: dict[int | str, dict[str, Any]] = {
    404: problem_response(404, None, "County or dataset release not found."),
    422: problem_response(422, None, "One or more request values are invalid."),
    429: problem_response(429, None, "Rate limit exceeded."),
    503: problem_response(503, "SERVICE_UNAVAILABLE", "Atlas data service is unavailable."),
}

EMPTY_COLLECTION_EXAMPLE = {
    "summary": "Illustrative exhausted page; no observations matched",
    "value": {
        "data": [],
        "meta": {
            "next_page_token": None,
            "response_at": "2026-01-01T00:00:00Z",
        },
        "links": {"self": None},
    },
}

MISSING_OBSERVATION_EXAMPLE = {
    "summary": "Synthetic missing-value example; not a published county result",
    "value": {
        "data": [
            {
                "observation_id": "example-observation",
                "measure_id": "case_count_floor_2023",
                "geography": {"geography_type": "county", "geography_id": "08001"},
                "period_start": "2023-01-01",
                "period_end": "2023-12-31",
                "temporal_grain": "year",
                "value": None,
                "value_state": "MISSING",
                "unit": "cases",
                "denominator": None,
                "source_id": "human",
                "methodology_id": None,
                "methodology_version": "example-transform-v1",
                "semantic_version": "example-semantic-v1",
                "release_id": "example-release",
                "provenance_ref": "example-observation",
                "limitations": ["Synthetic documentation example; no measured value is asserted."],
                "evidence": {"resource_type": "observation", "resource_id": "example-observation"},
            }
        ],
        "meta": {"next_page_token": None, "response_at": "2026-01-01T00:00:00Z"},
        "links": {"self": None},
    },
}

KNOWN_UNAVAILABLE_COLLECTION_EXAMPLE = {
    "summary": "Successful initial query with no published results; not negative evidence",
    "value": {
        "data": [],
        "meta": {
            "next_page_token": None,
            "response_at": "2026-01-01T00:00:00Z",
            "availability": "UNAVAILABLE",
            "reason": "NO_PUBLISHED_RESULTS",
            "release_id": "example-release",
            "limitations": [
                "No published observations match this selection in the checked release. "
                "This does not establish biological absence, source coverage, "
                "or geography membership."
            ],
        },
        "links": {"self": None},
    },
}

AVAILABLE_OBSERVATION_EXAMPLE: dict[str, Any] = deepcopy(MISSING_OBSERVATION_EXAMPLE)
AVAILABLE_OBSERVATION_EXAMPLE["summary"] = "Synthetic available published observation example"
AVAILABLE_OBSERVATION_EXAMPLE["value"]["data"][0].update(value=12, value_state="OBSERVED")
AVAILABLE_OBSERVATION_EXAMPLE["value"]["meta"].update(
    availability="AVAILABLE", release_id="example-release", limitations=[]
)

EXHAUSTED_OBSERVATIONS_EXAMPLE: dict[str, Any] = deepcopy(EMPTY_COLLECTION_EXAMPLE)
EXHAUSTED_OBSERVATIONS_EXAMPLE["summary"] = "Empty continuation page; not an unavailable selection"
EXHAUSTED_OBSERVATIONS_EXAMPLE["value"]["meta"].update(
    availability="AVAILABLE", release_id="example-release", limitations=[]
)

PENDING_GEOGRAPHY_EXAMPLE: dict[str, Any] = {
    "summary": "Confirmed county identity; canonical geography publication pending",
    "value": {
        "availability": "UNAVAILABLE",
        "reason": "CANONICAL_GEOGRAPHY_UNPUBLISHED",
        "data": None,
        "release_id": "example-release",
        "limitations": ["Illustrative checked county directory; no geometry is delivered."],
    },
}
