# County surveillance situations (API #253)

`GET https://api.carawaylabs.com/v1/counties/{fips}/surveillance-situations`
is an additive anonymous read. Production readiness requires the deployment
receipt and live canaries; the existence of this document does not prove deployment.
The authoritative schemas are `openapi.json` (public HTTP) and
`first-party-openapi.json` (complete first-party build artifact).
Operation ID: `county_surveillance_situations_get`.

The response contains canonical five-digit `county_fips`, active `release_id`
and `bundle_sha256`, and two distinct variant envelopes:

| Field | Persisted model | Display purpose |
| --- | --- | --- |
| `four_state` | `surveillance-situation-four-state` | Primary four-state surveillance situation; includes human surveillance |
| `tick_pathogen` | `surveillance-situation-tick-pathogen` | Secondary tick-and-pathogen grouping; excludes human surveillance |

Each envelope contains `model_id`, `availability`, `unavailable_reason`,
`compatibility`, batch identity, input release and nullable `data`.
Available `data` preserves model/version-scoped class ID, reviewed `class_name`
and `class_description` verbatim, all four `{class_id, probability}` pairs,
the published limitations, model/batch versions, payload/dictionary hashes,
input release/bundle, fit Git SHA, feature/config contract, model and source
provenance, source vintages, handoff approval and Data publication authorization.
`activated_at` is Data activation time, not source vintage or retrieval time.
The API performs no inference, fitting, thresholding, probability rescaling,
tier mapping or replacement with another batch.

Membership probabilities are finite numbers on 0–1, sum to one within the
producer's `1e-6` tolerance, and retain all four unique version-scoped class IDs.
The API validates that vector; it does not recompute the persisted class.
They are not probabilities of disease, pathogen presence, incidence, diagnosis,
causal effects or Tier 1 review priority. The tick/pathogen class 0 groups
pathogen present and no pathogen record; its membership is not pathogen presence.
`No records` is a category, never evidence of biological absence. Alaska/Hawaii
Unknown is a valid available class, never missing data. Class IDs and labels
must be interpreted within their model and version, never compared by index alone.

| Outcome | HTTP / body |
| --- | --- |
| Approved compatible persisted assignment | 200, variant `AVAILABLE`, reason null, exact stored `data` |
| Confirmed unpublished/withdrawn/unapproved/incompatible/missing assignment | 200, affected variant `UNAVAILABLE`, null `data`, persisted reason |
| Invalid FIPS format | 422 RFC 9457 Problem Details |
| Not in current canonical county directory | 404 `RESOURCE_NOT_FOUND` |
| Dependency failure, missing availability records, duplicates, bad persisted values or release change | 503 Problem Details; never converted to unavailability |

Unavailable reasons are `NOT_PUBLISHED`, `DEACTIVATED`, `UNAPPROVED_BATCH`,
`INCOMPATIBLE_RELEASE`, and `MISSING_ASSIGNMENT`. One variant may be available
while the other is unavailable. `EXACT_INPUT` means both input release and
bundle equal the active release/bundle. `ADMITTED_CARRY_FORWARD` means the
governed serving view admitted the immutable output for a different active
release/bundle. API code never admits a carry-forward itself. Unavailable
variants have compatibility `UNAVAILABLE`.

All successful responses use `Cache-Control: no-store`. There is no API result
cache, ETag or stale fallback. A browser query cache should include FIPS and
the returned release/bundle and both model/batch versions when retaining results;
do not relabel cached output as current after a release change. Reads use the
existing instrumented shared Snowflake connection and configured query timeout.
There are at most four bounded SELECTs: current release (limit 2), requested
county identity (limit 2), requested county's joined availability/results (limit 3
to detect duplicates), and release recheck (limit 2). No FEATURE_STORE access,
Snowflake writes, full county snapshot load, or unrestricted SQL is introduced.
Existing CORS, rate limits, request IDs, redaction and dependency telemetry apply.

## Production-authoritative examples and Web #650 handoff

`tests/fixtures/surveillance-situations-prod-2026-10-19.json` contains 12 real
joined availability/result rows read on October 19 with the named PAT connection
`ATLAS_PROD_READ`, human user `MATTHEWCARAWAY`, role `OH_LYME_PROD_READ`, database
`ONE_HEALTH_LYME_GAP_ATLAS_PROD`, warehouse `COMPUTE_WH`. This is authoritative
production data and SQL integration evidence, not a new actual-service-identity
claim. The actual API identity foundation remains PR #272/run 39307519146.
The fixture is a snapshot; its tests do not prove current production deployment.

| FIPS | Primary class | Secondary class |
| --- | --- | --- |
| `01003` | Established blacklegged tick (0) | Established blacklegged tick (0) |
| `02013` | Unknown tick and pathogen status (1) | Unknown tick and pathogen status (3) |
| `04015` | Western blacklegged tick recorded (2) | Western blacklegged tick established (2) |
| `01001` | Pathogen not recorded (3) | Established blacklegged tick (0) |
| `01009` | Pathogen not recorded (3) | Blacklegged tick not established, pathogen not recorded (1) |
| `15001` | Unknown tick and pathogen status (1) | Unknown tick and pathogen status (3) |

Both models and batch are `v1`; batch ID `surveillance-situation-batch`.
Input release `governed-2026-10-15-january-climate-prod`; bundle
`53e50aa37c701635e96c48cd4931c4c4a9e7e695c146263fb9bb335e541ae74d`.
Fit SHA `cd936fe345664e606c916b137dd642caa0d6d73b`.

For Web, copy this PR's `first-party-openapi.json` to `contracts/openapi.json`
and run its existing `npm run generate:api`, generating React Query client,
TypeScript models and Zod validators. Do not use the public projection as that
first-party input. Add display/loading/typed-unavailable/error behavior under
Web #650 with provenance access and a non-map result path. This API story
does not implement the Web interface or change Tier 1.

Before declaring #253 complete, record the reviewed deployed source SHA,
ACTIVE deployment ID/run, live six-county responses and 404/422 results, and
post that receipt with generated contract links to Web #650. Negative
publication/compatibility/dependency branches use controlled tests; do not
withdraw production batches, change release pointers or break dependencies
to manufacture live negative cases. Existing protected main promotion and
rollback apply; no deployment topology or scientific decision changes.
