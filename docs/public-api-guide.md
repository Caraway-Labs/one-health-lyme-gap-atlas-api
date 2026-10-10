# Public Atlas API guide

Base URL: `https://api.onehealthatlas.org`. The canonical public API is V1 and
serves anonymous, read-only JSON without an API key. Collections have `data`,
`meta`, and `links`; details have `data`. Errors are RFC 9457
`application/problem+json` with a stable `code`, HTTP `status`, `detail`, and
`request_id`. The OpenAPI application version is separate from the V1 contract.

Use the [interactive API reference](https://api.onehealthatlas.org/docs),
[readable reference](https://api.onehealthatlas.org/redoc), or
[public OpenAPI JSON](https://api.onehealthatlas.org/openapi.json).
The [governed V1 contract](public-api-v1-contract.md) explains semantic policy.
These routes publish current-release metadata and county observations. They
do not provide historical-release selection, arbitrary aggregation, or geometry.

## Discover and query

The following `curl` requests use IDs verified in the production release:

```sh
curl -fsS 'https://api.onehealthatlas.org/v1/indicators?page_size=10'
curl -fsS 'https://api.onehealthatlas.org/v1/measures?indicator_id=human_disease_burden&page_size=10'
curl -fsS 'https://api.onehealthatlas.org/v1/measures/case_count_floor_2023'
curl -fsS 'https://api.onehealthatlas.org/v1/observations?measure_id=case_count_floor_2023&geography_type=county&geography_id=08001&year=2023'
```

The `human_disease_burden` indicator lists `case_count_floor_2023` in
`measure_ids`. Use this canonical `measure_id` in observations. Its unit is
`cases`; the published values are privacy-protected **floors**, not complete
incidence. Discovery may include measures without published county rows.
County FIPS must be five-character strings, preserving leading zeros.

Compare counties by repeating `geography_id`; use either `year` or a complete
`start_date`/`end_date` pair. The currently published county observations
contain **only the annual 2023 period**. A date range selects available periods;
it does not imply historical depth. No strata are governed in this release.

```sh
curl -fsS 'https://api.onehealthatlas.org/v1/observations?measure_id=case_count_floor_2023&geography_type=county&geography_id=08001&geography_id=08003&year=2023&page_size=1'
curl -fsS 'https://api.onehealthatlas.org/v1/observations?measure_id=case_count_floor_2023&geography_type=county&geography_id=08001&start_date=2023-01-01&end_date=2023-12-31'
```

Read `meta.next_page_token`; if non-null, send it as `page_token` with every
other query parameter unchanged. Continue until null. Default `page_size` is
100; allowed sizes are 1–500. Ordering is deterministic by measure ID, county
FIPS, period start, then observation ID. Tokens are opaque, signed, and bound
to the query and release. A tampered, incompatible, or pre-restart token returns
400 `INVALID_REQUEST`; restart at page one. Tokens are not durable citations.

This Python example uses the common `requests` client (`python -m pip install
requests`) and traverses both counties without a maintained Atlas SDK:

```python
import requests

base = "https://api.onehealthatlas.org"
params = [
    ("measure_id", "case_count_floor_2023"),
    ("geography_type", "county"),
    ("geography_id", "08001"),
    ("geography_id", "08003"),
    ("year", "2023"),
    ("page_size", "1"),
]
with requests.Session() as session:
    while True:
        response = session.get(f"{base}/v1/observations", params=params, timeout=30)
        response.raise_for_status()
        page = response.json()
        for observation in page["data"]:
            print(observation["geography"]["geography_id"],
                  observation["value_state"], observation["value"])
        token = page["meta"]["next_page_token"]
        if token is None:
            break
        params = [(key, value) for key, value in params if key != "page_token"]
        params.append(("page_token", token))
```

## Follow provenance and methodology

Read each observation's `source_id` and `methodology_id`, then request those
resource IDs. For this live measure they are `human` and
`human_confirmed_probable_case_floor_v1`:

```sh
curl -fsS 'https://api.onehealthatlas.org/v1/sources/human'
curl -fsS 'https://api.onehealthatlas.org/v1/methodologies/human_confirmed_probable_case_floor_v1'
```

The source describes publisher, `dataset_id`, `lineage_source_id`, source URL,
vintage, and limitations. The method provides description, version, and
limitations. Read observation limitations as well. `source_id` is the public
resource key; lineage source and dataset IDs are separate identities.
`provenance_ref` and `evidence` identify the observation, not a literature
citation. Observation `methodology_version` is the transformation version,
distinct from methodology resource `version` and
`release_methodology_version`.

### Value states

Read `value_state` with `value` and `unit`:

| Public V1 state | Meaning |
| --- | --- |
| `OBSERVED` | Governed published value; inspect its limitations. |
| `ZERO` | Governed zero, distinct from absent evidence. |
| `MISSING` | No governed value; never replace with zero. |
| `SUPPRESSED` | Value withheld under source or governance rules; do not infer it. |
| `UNAVAILABLE` | Value unavailable; do not infer zero. |
| `NO_COUNTY_LINKED_RECORD` | Literal `no_county_linked_record`; no linked record is not evidence of absence. |

These are the public schema states; not every measure exhibits each one.
The API does not coerce other internal states into this enum. A published
surveillance floor is not a diagnosis, prediction, causal claim, exposure map,
or individual risk estimate.

### Freshness and versions

| Field | Meaning |
| --- | --- |
| `period_start`, `period_end` | Observation period, presently annual 2023. |
| `source_published_at`, source `upstream_updated_at` | Upstream publication or update time when governed. |
| `atlas_acquired_at`, source `source_retrieved_at` | Atlas acquisition or source retrieval time when governed. |
| `atlas_processed_at` | Atlas processing time when governed. |
| `methodology_version`, method `version` | Transformation and methodology resource versions. |
| `semantic_version` | Semantic schema version. |
| `release_id`, source/method `release_version` | Governed release identity. |
| `meta.response_at` | Response generation time, not source freshness. |

Explicit `null` means Atlas does not govern that timestamp or value for this
record. Source vintage (for example `2023`) is not a publication timestamp.
Do not combine these fields into a generic “last updated.”

## Bounds, errors, and caching

An observation request needs one measure, `geography_type=county`, one or more
five-digit county FIPS, and either `year` or both dates. At most 500 counties
may be selected. The estimated logical-result ceiling is 10,000; over-broad
queries fail before execution rather than truncate. Query strings are capped
at 8,192 bytes. Unsupported filters, strata, sorting, and aggregation are not
silently ignored.

| Example | HTTP | Problem `code` |
| --- | ---: | --- |
| Unknown observation measure | 404 | `RESOURCE_NOT_FOUND` |
| County FIPS `801` | 400 | `INVALID_REQUEST` |
| Observation `geography_type=state` | 400 | `UNSUPPORTED_FILTER` |
| Unsupported `domain=x` | 400 | `UNSUPPORTED_FILTER` |
| `stratification=sex:female` | 400 | `UNSUPPORTED_STRATIFICATION` |
| Invalid/tampered `page_token` | 400 | `INVALID_REQUEST` |
| Request limit exceeded | 429 | rate-limit Problem Details plus `Retry-After` |

For example, an invalid measure yields this RFC 9457 shape (request ID varies):

```json
{"type":"https://carawaylabs.com/problems/resource-not-found","title":"Invalid public query","status":404,"detail":"Measure has no published county observations.","instance":"/v1/observations","request_id":"<per-request ID>","code":"RESOURCE_NOT_FOUND","errors":null}
```

Handle `status` and `code`; retain `request_id` for support. Anonymous public
reads are limited to 60 requests per rolling minute and five simultaneous
requests per connecting address. A 429 has `Retry-After` (one to 60 seconds)
and `Cache-Control: no-store`; wait before retrying. Successful current-release
reads use `Cache-Control: public, max-age=60, must-revalidate`. Stable detail
responses carry an `ETag`; send `If-None-Match: <ETag>` to get an empty 304 if
unchanged. Collections have no ETag because `meta.response_at` changes.

## Reproduce and cite

Save the exact request URL/parameters, measure ID, county FIPS, queried and
returned periods, `release_id`, `semantic_version`, `methodology_id` and
versions, `source_id`, `lineage_source_id`, `dataset_id`, source URL/vintage,
retrieval date, and limitations. Save the response or its checksum when exact
later reproduction matters: these routes read the **current** release and a
future release may change the result of the same URL.

Practical attribution: “One Health Lyme Gap Atlas, public API V1,
`case_count_floor_2023`, county FIPS 08001, 2023 period, release
`governed-2026-09-18-unknown-coverage`, semantic version `1.0.0`, retrieved
[UTC date]; upstream: Centers for Disease Control and Prevention, dataset
`x5j9-wybp` (2023 vintage), [source URL], with Atlas limitations.” Replace
these examples with values from your response. This is traceability guidance,
not a legal citation requirement.
