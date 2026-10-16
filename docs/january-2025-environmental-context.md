# January 2025 environmental context (API #84)

This additive draft consumes the reviewed DATA #443 presentation contract through
the existing `/v1/measures`, `/v1/indicators` and `/v1/observations` resources. It
creates no source-specific public endpoint. `ENVIRONMENTAL_CONTEXT_ENABLED` is
false by default. It has not been enabled, deployed or accepted against live data.

Use canonical measure IDs `nclimgrid_prcp_county_day`, `nclimgrid_tmin_county_day`,
`nclimgrid_tmax_county_day` and `nclimgrid_tavg_county_day`. TAVG is NOAA supplied.
For example, after governed publication and enablement:

```text
/v1/observations?measure_id=nclimgrid_prcp_county_day&geography_type=county&geography_id=08001&start_date=2025-01-01&end_date=2025-01-31
```

The existing 500-county, 500-row page, 10,000-result configured ceiling, query
timeout, rate limiting, Problem Details and signed query/release-bound pagination
apply. Daily selection counts calendar days even when `year` is used. No sorting,
aggregation or strata are accepted. Unsupported dates match no published records;
this does not infer missing weather or authorize historical ingestion.

The additive optional `Observation.environmental_context` contains coverage state,
source-time presence, expected/intersected/supported/valid areas, both fractions,
labeled-day convention, upstream modification metadata, metadata revision and
weight/geometry versions. It exposes no polygons, internal capture/run/artifact
IDs, hashes or warehouse payloads. Annual observations retain their existing
meaning and may carry a null environmental_context.

PRCP uses millimeters; temperatures use degrees Celsius. Valid numeric zero,
negative temperature, MISSING with independent PARTIAL_COVERAGE or SOURCE_MISSING,
and UNAVAILABLE remain distinct. Daily completeness is valid/source-supported
area at the existing 0.95 threshold; spatial support is supported/legal-county
area. Neither fraction is a disease-risk score. Alaska/Hawaii have unavailable
values. January weather is descriptive; no causal, risk, predictive or ML
admission claim is made.

Observation dates label NOAA's early-morning-ending 24-hour period. They are not
midnight calendar aggregates. `atlas_acquired_at`, `source_published_at` and
`upstream_date_modified` stay separate; unknown historical first availability is
not filled from acquisition or modification time. Native DOUBLE quantities use
the view's guarded native connector float. DECIMAL nonzero quantities may be
exact numeric strings; this is documented in OpenAPI rather than silently rounded.
Integer and zero values remain numeric. Transport helper columns are removed.

DATA V136 is DEV-only and supplies the two named climate presentation views. No
PROD object parity is presumed. Both metadata discovery and observations fail
closed if access, publication, decoding or current-release consistency fails.
Disabled code opens no additional climate connections for annual consumers.

Before enabling, require independent review and exact-head CI, actual retained
NOAA/TIGER catalog/version decisions, reviewed metadata, full target canonical
candidate and frozen membership verification, protected environment-specific
publication/rollback, and authorized view-only reader access. Run
`scripts/verify_environmental_reader.py` inside the existing authorized API
environment with its real configured service identity. The script uses only
SELECT and returns bounded identity/count proof, not observations or secrets.
A human PAT or fixture pass cannot satisfy service acceptance. No grants or
credential configuration are included in this draft.

Both OpenAPI exports derive from the existing exporter. Web remains unchanged
under the explicit task scope. Generated-client coordination is a downstream
handoff, not a completed Web validation claim. Keep #84 open until a published
source-backed county request and actual production behavior are independently
accepted. Disable the flag to withdraw climate consumption without changing the
annual endpoint; the existing DATA pointer rollback controls published membership.

## API service probe cancellation — October 15, 2026

The Data publication and scientific readback passed in DATA workflow
38812216717. Actual API deployment `ec0a37cd-025e-43ba-9b55-0511e254b592`
verified its configured service identity and read four climate metadata rows, but
its observation read failed. Environmental context remains disabled.

The owner supplied the retained query-history record:
`01c7c212-030c-3282-0064-2d0701259d06`, error `000604`, `SQL execution canceled`,
start `2026-10-16T01:22:15.013Z`, end `2026-10-16T01:22:20.138Z`, total 5,125ms,
execution 4,525ms, queue 0ms, warehouse `COMPUTE_WH`. These are server records for
an already-executed query, not a new diagnostic run. The timing matches the
probe's five-second cancellation ceiling; it is not evidence of absent objects,
a missing grant, or insufficient warehouse capacity.

The existing three-SELECT probe retains its five-second-or-lower configured
statement limit, one-row observation cap, all primary-identity assertions and
same-release/four-measure checks. Its observation SELECT now uses the previously
accepted January point: `nclimgrid_prcp_county_day`, county `48277`, labeled day
`2025-01-17`. It reads the same governed current view; no RAW/revision-table or
lineage bypass is introduced. The prior unfiltered LIMIT bounded returned rows,
not the candidate scan. This correction narrows the query to a real consumer
shape; it does not prove a runtime improvement until a fresh authorized service
attempt passes. Later public HTTP decoding remains a separate gate.

Snowflake cancellation code 604 is reported as the closed category
`query_canceled`, distinct from an unavailable object or generic dependency
failure. The category does not assert whether cancellation came from a client,
server timeout or explicit cancellation. SDK text, SQL and error codes are not
copied into operational output. No retry is added.

The failed attempt remains consumed. Human merge is required under
`AGENTS.md` and `docs/ci-and-production-deploy.md`; merging green main triggers
the existing production deployment and a fresh startup probe. A new one-attempt
execution authorization must cover that startup before merge. Keep the feature
flag disabled until the actual service probe passes on the intended published
release. Use only the existing flag/configuration and canonical HTTP resources
for subsequent acceptance; do not create another reader or endpoint.
