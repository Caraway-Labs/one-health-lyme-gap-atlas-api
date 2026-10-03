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
