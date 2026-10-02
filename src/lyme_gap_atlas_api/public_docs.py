"""Framework-native documentation metadata; no request or data behavior."""

from typing import Any

API_SUMMARY = "Governed Atlas analytics, metadata, and provenance"
API_DESCRIPTION = """Read-only structured data from the One Health Lyme Gap Atlas.
Ordinary public reads are anonymous and require no API key.

## Interpretation and reproducibility
V1 (`/v1`) is the HTTP contract version; the application version is separate.
Read source, methodology, semantic/release versions, and limitations together.
Surveillance floors and gap scores are not diagnoses, individual risk estimates,
or causal claims. Null, zero, suppressed values, and no linked records have
distinct meanings. Response time is not publisher freshness.

## Queries and errors
Canonical collections use `data`, `meta`, and `links`; details use `data`.
Pagination defaults to 100 items (maximum 500). Send opaque `next_page_token`
as `page_token` with the same filters; null means exhaustion. Tokens bind to
query/release and may expire on restart. Current-release observations require
one measure, 1–500 county FIPS IDs, and a year or complete inclusive date range.
The default pre-execution logical-result ceiling is 10,000, not truncation.
Only the annual 2023 period is currently published; no strata are supported.

Errors use RFC 9457 `application/problem+json` with `request_id` for support.
Canonical errors carry stable `code` values; legacy Atlas routes retain their
existing error semantics (including 422 validation). Respect `Retry-After` on
429/503. Canonical metadata/observations cache for 60 seconds; supported detail
resources return ETags and 304 on matching `If-None-Match`. See each operation
for legacy cache and export behavior.

## Contract consumers
Fumadocs consumes the external `public-openapi.json` at `/public/openapi.json`.
The complete first-party `openapi.json`, `/docs`, and `/redoc` remain engineering
surfaces for existing product clients. Existing operation IDs remain stable for
generated clients. Breaking public changes require an approved migration and
deprecation plan; optional additive fields may appear within V1.
Only separately approved ML outputs may be published. Internal conversational
AI, literature retrieval, account, and feedback product routes are excluded.
"""

TAGS = [
    {"name": "discovery", "description": "Current-release indicators and measurable variables."},
    {
        "name": "observations",
        "description": "Bounded county observations with explicit value states.",
    },
    {
        "name": "provenance",
        "description": "Governed source and methodology identities and versions.",
    },
    {
        "name": "geographies",
        "description": "Typed geography identity; delivery is not yet available.",
    },
    {"name": "atlas", "description": "Existing release analytics and separate display geometry."},
    {"name": "counties", "description": "Existing county details and versioned PDF reports."},
    {"name": "states", "description": "Existing state PDF reports."},
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


def problem_response(status: int, code: str | None, detail: str) -> dict[str, Any]:
    """Describe the existing RFC 9457 wire format using a synthetic example."""
    value: dict[str, Any] = {
        "type": f"https://carawaylabs.com/problems/http-{status}",
        "title": "Request failed",
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


LEGACY_ERRORS: dict[int | str, dict[str, Any]] = {
    404: problem_response(404, None, "County or dataset release not found."),
    422: problem_response(422, None, "One or more request values are invalid."),
    429: problem_response(429, None, "Rate limit exceeded."),
    503: problem_response(503, None, "Atlas data service is unavailable."),
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
