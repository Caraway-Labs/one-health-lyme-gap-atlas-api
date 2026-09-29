# Canonical public API V1 contract (API #52)

The FastAPI-generated `openapi.json` is the HTTP authority; `/openapi.json`, `/docs`,
and `/redoc` render the same application contract. This document records policy
and handoff boundaries that an HTTP schema cannot fully express. The canonical
routes are additive to all current `/v1/atlas/*`, county, report, feedback,
chat, and authenticated `/v1/me/*` routes. API #53 connects indicator and measure
discovery to governed metadata views. API #54 publishes the bounded current-release
county observation projection. Geography, source, and methodology handlers remain
controlled 503 pending their owning stories.

## Resources and semantics

`GET /v1/indicators[/{indicator_id}]` describes public-health concepts;
`GET /v1/measures[/{measure_id}]` describes distinct measurable variables.
`GET /v1/observations` selects one required measure over typed geography and
bounded time. `GET /v1/geographies/{geography_type}/{geography_id}` uses
`county` plus five-digit FIPS or `state` plus two-digit FIPS, where supported.
`GET /v1/sources[/{source_id}]` identifies evidence origin;
`GET /v1/methodologies/{methodology_id}` identifies production and
interpretation method. Physical warehouse relations are not resource IDs.
Geometry is separate from geography identity and this contract grants no
analysis-grade geometry access (workspace ADR 0003; API #85).

Collections have `data`, `meta`, and `links`. Single resources have `data`.
`page_size` defaults to 100 and cannot exceed 500; `meta.next_page_token` is
nullable when exhausted. The issuing service generates opaque tokens
bound to query, sort continuation, and release state, reject incompatible reuse,
and must not make clients parse them. Observation ordering is measure ID,
county FIPS, period start, then stable observation ID. No arbitrary sorting or
aggregation is offered.

Each observation retains value plus governed `value_state`, typed geography,
period, source, method/version, semantic version, release, provenance reference,
limitations, and compact evidence reference. Publisher update, Atlas acquisition,
Atlas processing, and response timestamps are distinct; machine timestamps
carry UTC offsets. The evidence reference is for structured Atlas resources;
PMID/PMCID/article/passage provenance stays in the literature domain. A mapping
to CDC MMG, PHIN VADS, USCDI, LOINC, or FHIR is optional reviewed metadata from
data #469–#473 and does not assert live clinical exchange.

The public V1 `value_state` enum is `OBSERVED`, `ZERO`, `MISSING`,
`SUPPRESSED`, `UNAVAILABLE`, and `NO_COUNTY_LINKED_RECORD`. The latter preserves
the governed categorical status with literal value `no_county_linked_record`;
it is neither null nor zero. `NOT_APPLICABLE` and `INCOMPLETE`
are **not** observation value states.
Applicability describes whether a measure applies to a geography, period, and
stratification context. Completeness/quality describes evidence coverage or
fitness; an `OBSERVED` value can still have incomplete supporting evidence.
These are independent semantic dimensions, not mutually exclusive replacements
for `value_state`. An adapter must never convert an unknown or missing value to
`NOT_APPLICABLE` or `INCOMPLETE`, or synthesize applicability/completeness when
the governed semantic layer does not supply them. Reviewed governed metadata
may later be exposed as optional, additive fields without reinterpreting
`value_state`. No speculative observation fields are present in this schema.
The Data semantic consumer schema currently permits four other states:
`UNKNOWN`, `NOT_REPORTED`, `NOT_DEFENSIBLE`, and `NO_RECORDS`.
The public V1 enum does not silently map any of them
to another state. #54 fails explicitly if a selected governed
observation has a state this public contract cannot represent; it must not
publish a coerced value state. Broader public state support requires
a reviewed API contract change that preserves the Data meanings.
Data [#191](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/issues/191)
owns applicability and quality metadata semantics; its machine-readable
consumer projection is Data [#194](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/issues/194).
See `atlas-semantic-domain-v1.md` and `atlas-semantic-consumer-v1.schema.json`.
#54 reads only `PRESENTATION.CURRENT_COUNTY_OBSERVATIONS_V`. The current release
publishes `human_status`, `case_count_floor_2023`, and `incidence_floor_2023`,
each for the inclusive 2023 calendar year. There is one period per county and
measure; a date range returns every governed period it contains without
implying historical periods. No strata are currently governed. Unknown
denominators and methodology resource IDs remain null; the response preserves
the available source key, URL, vintage, retrieval time, method text and versions,
and limitations. The observation ID is the stable compact provenance reference.
It must preserve future
applicability and completeness/quality semantics separately when supplied by the
governed layer and must not fabricate either dimension.

## Bounds and access

`measure_id`, one or more typed `geography_id` values, and either `year` or a
complete `start_date`/`end_date` pair are required. Conflicting time selectors,
invalid FIPS, unsupported filters/strata, or unsupported measure grain fail
explicitly with 400 Problem Details. The default estimated logical-result
ceiling is 10,000; this is a pre-execution bound, never truncation. #54 must
estimate from resolved measure geography/time/stratum dimensions before its
warehouse call. #54 uses the governed annual grain to bound requested years
times county count before querying; pagination never truncates the result.

Ordinary canonical reads are anonymous: no Atlas account, Supabase token, or
API key. Initial configurable policy defaults are 60 requests/minute/IP and
5 concurrent requests/IP, using only transient address information; #56 owns
enforcement, telemetry, 429/Retry-After tuning, and production caching. API
keys would need demonstrated quota, attribution, partner, SLA, or restricted
resource need. Private `/v1/me/*` remains authenticated. Interactive reads
are not bulk export; immutable downloads need a separate decision.

Public errors use RFC 9457 `application/problem+json` with stable `code`:
`INVALID_REQUEST`, `RESOURCE_NOT_FOUND`, `UNSUPPORTED_FILTER`,
`UNSUPPORTED_STRATIFICATION`, `QUERY_TOO_BROAD`. Existing error infrastructure
is reused. `CANONICAL_DATA_UNAVAILABLE` denotes the #52 contract-only handlers.
The first four client errors require the appropriate downstream service to
classify semantics; #52 already enforces request shape and breadth.

## Compatibility and ownership

Additive V1 changes include new routes, optional fields, resources, supported
filter values, and reviewed mappings. A new major version is required for
required-field removal/rename, changed meaning, incompatible value-state,
identifier, pagination or query semantics, or measure unit/meaning changes
without a new identity. Atlas intends at least 90 days' retirement notice where
operationally practical. Current first-party consumers do not need a synchronized
migration. Web #284 owns the full consumer inventory and migration matrix; this
API keeps a concise current-route crosswalk below. No route is retirement-ready
until equivalent semantics, consumer migration, tests, telemetry where available,
docs, and a reviewed retirement decision are recorded.

| Current API route | Known consumer | Canonical counterpart | Status / retirement |
| --- | --- | --- | --- |
| `/v1/atlas/metadata` | Atlas Web | sources, methodologies, indicators/measures where equivalent | Web #284 inventory pending; retain |
| `/v1/atlas/scores`, `/v1/counties/{fips}` | Atlas Web | observations only for reviewed equivalent measures | Web #284 inventory pending; retain |
| `/v1/atlas/geometry` | Atlas Web map | separate display geometry under API #85 | retain |
| `/v1/atlas/ranking.csv` | Atlas Web export | none; action/export | retain |
| county/state report PDF | Atlas Web | none; action | retain |
| `/v1/me/*` | Atlas Web account | none; private | retain |
| `/v1/knowledge-graph/chat` | Research Assistant | none; conversation | retain |
| `/v1/feedback` | Atlas Web | none; action | retain |

Legacy/current REST and canonical REST are peer HTTP adapters to API-owned
application services. MCP and AI/RAG can reuse portable shared domain contracts
and in-process services; they do not need to call the public REST endpoint.
Snowflake query construction, credentials, HTTP policy and persistence remain
API-owned. Literature retrieval, Neo4j traversal, prompts and answer generation
are outside #52 (API #96–#99 and `docs/api-layer-inventory.md`).

## Indicator and measure discovery (#53)

The API reads `PRESENTATION.CURRENT_INDICATOR_METADATA_V` and
`PRESENTATION.CURRENT_MEASURE_METADATA_V` through its configured presentation
schema and least-privilege read role. IDs and release/schema versions come from
the views. Indicator `measure_ids` join by indicator ID and release version.
Lists sort by stable ID. Missing detail IDs return 404 Problem Details;
unsupported query keys return 400 `UNSUPPORTED_FILTER`.

Collections accept exact `indicator_id` or `measure_id` where applicable;
measure collections also accept exact `indicator_id` and literal stored
`geography_type` (such as `COUNTY_FIPS_5`, distinct from the observation
geography enum). `domain`, `category`, and `availability` are unsupported.
Availability is never inferred from observation presence. `page_size` is 1–500
with default 100. Opaque page tokens bind offset, filter, and current release;
reuse with a different filter or release fails. Responses use
`Cache-Control: public, max-age=60, must-revalidate`.

The view's `description` maps to `definition`; `semantic_contract_version`
maps to `semantic_version`, with `release_version` exposed separately.
`geography_semantics` and `temporal_semantics` retain the stored literals.
The stored `measure_type` is a data type and `methodology` is text, not a
methodology resource ID. Unknown denominator, strata, source association,
standards mapping, domain, category, and measure definition remain null.
No observation rows are scanned to fill them. Data #499 verified DEV; protected
PROD promotion of V123/V124 and the read grants remains necessary for live
PROD endpoint readiness. Observation, source, and methodology routes remain
controlled 503 under their downstream stories.
