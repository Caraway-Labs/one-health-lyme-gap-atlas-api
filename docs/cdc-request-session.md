# API #248 Stage 1: exclusively request-owned CDC sessions

Owner authorization: API #248 Stage 1, October 17, 2026. This implements the
existing service/producer boundary (ADR 0017 and API #246's current-release
binding). It changes no public schema, operation ID, scientific meaning,
authentication model, Data dependency pin, Snowflake object or deployment topology.

## Ownership and invariants

The synchronous service validates supported geography, strata, cumulative date
and service configuration before acquiring a session. For an enabled CDC request,
the existing repository context pattern creates one connection and a local
repository borrowing it. A local CDC adapter borrows that same connection; the
application-scoped repository/adapter are never mutated. Session timeout and
secondary-role disable run once before the release lookup. Each subsequent
county uses its own bounded cursor, sequentially, on that session. There is no
pool, cross-request session cache, mutable-release cache, parallel query or batch
producer. The connection exits after the complete page/envelope or any failure.

The unchanged pinned Data `_project(query, ...)` is invoked for every processed
county. Its intended-reader identity SELECT remains per county. The same secure
authority SQL, binds, `fetchmany(2)`, metadata revision 3, exact source/record/
semantic/donor receipt and release admission checks remain. No identity or
record validation is memoized. The captured current release must still match the
exact approved release; no historical fallback is introduced. Standalone CDC
operator `_read` still owns its own session and configures it before projection.

Statement arguments remain `min(public_query_timeout_seconds, 30)` for CDC;
current-release lookup retains its existing configured bound. No new overall
HTTP timeout or synchronous-worker cancellation mechanism is introduced. A
checkpoint, producer exception, SDK timeout or cancellation unwinds the context;
client abandonment does not establish that the worker has unwound. If the
connection's context cleanup raises during an existing body failure, preserve
the original failure (including cancellation), rather than turning it into a
different scientific/public result.

For CDC only, if SDK context teardown raises before closing (for example,
implicit COMMIT/ROLLBACK failure), the owner also attempts explicit close and
preserves the original body error. A teardown-only failure becomes the existing
sanitized unavailable response. Fallback cleanup is best effort if the SDK's
close itself fails; no code can guarantee network completion in that case.

Pagination still processes at most `page_size + 1` sorted selected counties,
performs admission on lookahead, and trims only afterward. A continuation reads
the current release again and repeats the prior lookahead read. Failure in any
processed county, including lookahead, fails the whole request; no partial success.
Human, climate and county-context selection/SQL/mapping are unchanged.

## Explicit operation budget

For k processed counties, connections change `1+k -> 1` and explicit executes
change `1+4k -> 3+2k`: two session setup statements, one release lookup, and one
identity plus one authority read per county. SDK implicit setup/transaction
statements remain outside this application counter and inside lifecycle timing.

| Counties / full page | Connections before -> after | Explicit executes before -> after |
|---:|---:|---:|
|1|2 -> 1|5 -> 5|
|2|3 -> 1|9 -> 7|
|5|6 -> 1|21 -> 13|
|10|11 -> 1|41 -> 23|

Two-county page_size=1 plus continuation changes total connections `5 -> 2` and
explicit executes `14 -> 12`. It still processes three county records across
the two requests. No authority-query count reduction is claimed.

## Measurement and verification

See the [telemetry contract](api-telemetry-contract.md#cdc-request-timings-api-248-stage-1)
for boundaries, overlap, closed dimensions, counters and failure coverage.
Connection/authentication, execute, fetchmany and teardown are timed at existing
SDK boundaries. Producer and API mapping are separately identified. SERVER
spans remain the final-body boundary; structured completion logs bound the
service but end before response-body transport. Existing sampling stays unchanged.

The investigation worktree and gitignored receipts remain preserved at
`C:/codex_programming/worktrees/api-248-latency-investigation/.agent-tools/api248/`.
Historical production baseline: singleton 6.389–7.097s, two 9.804–10.578s,
five 21.032–22.142s. Original two-county outlier 26.860s correlated to 26.678s of
middleware processing. These are historical HTTP measurements, not candidate
results. The owner reports a separate Snowflake investigation identified secure
authority-view compilation of 5–9.6s on some statements. Session reuse does not
change that query or promise to eliminate compilation.

Focused regression tests cover ownership, setup, serial query/bind/fetch bounds,
request isolation, runtime identity, failures/cancellation, scientific/provenance
states, fail-closed real producer behavior, pagination, timing, redaction and
non-CDC regressions. Controlled local benchmarks must label all injected costs
and distinguish fake-SDK elapsed time from actual production improvement.

### Reproducible local comparison

`uv run python scripts/benchmark_cdc_sessions.py` loads the accepted baseline
modules from Git at `ca65b7b689b6b417b122f25ff3597ddbdf36f98b`. It runs both
measures at one, two and five counties twice per mode (24 service calls total).
The controlled SDK injects 100ms per connection, 10ms per explicit execute,
2ms per fetch and 10ms per connection exit. The producer callback is a synthetic
test double; unchanged Data admission is verified separately, not benchmarked
as a production authority decision. Every before/after public data list is
equal and every fake session closes. Generated envelope timestamps are excluded
from equivalence; the observation fields/provenance are included.

Observed local service wall-clock medians in milliseconds, N=4 per row/mode
across the two measures. Injected durations are assumptions; measured durations
include scheduler overhead. These are not FastAPI/network or production timings.

| Counties | Service before -> after | Connect before -> after | Execute before -> after | Fetch before -> after | Producer processing before -> after | Teardown before -> after |
|---:|---:|---:|---:|---:|---:|---:|
|1|296.51 -> 180.86|201.39 -> 100.61|52.83 -> 52.78|7.76 -> 7.47|1.01 -> 0.78|20.92 -> 10.33|
|2|493.11 -> 218.12|314.02 -> 100.64|95.77 -> 73.12|12.06 -> 12.62|1.90 -> 1.50|40.47 -> 10.40|
|5|979.58 -> 316.84|602.85 -> 100.30|219.74 -> 137.09|26.83 -> 26.56|4.83 -> 3.45|62.91 -> 10.38|

CSV retains all 24 samples and inclusive producer time. Inclusive producer and
exclusive callback processing are nested; never add both. Raw CSV remains
gitignored under `.agent-tools/api248-stage1/benchmark.csv`. No delay reduction
is assigned to the authority-query compilation that this change leaves intact.

### Acceptance invariant matrix

| Invariant | Evidence |
|---|---|
| Same admitted release, revision 3, source/value/provenance projection | Unchanged Data pin and DTO expressions; both-measure existing CDC tests, before/after DTO equality |
| Full per-county authority and intended-reader checks | Same `_project` callback, unchanged identity/authority SQL/binds/fetch bound; real producer missing-authority/wrong-role/database rejection tests |
| Atomic failure, no partial page or lookahead success | Second-county timeout and release/metadata/geography/private-field failure tests |
| Session owns one request, serial queries, no shared mutation | Counts for both measures at 1/2/5/10; overlapping-request isolation; shared adapter/repository remain unbound |
| Cleanup across success, exceptions, fetch failure and cancellation | Lifecycle tests plus SDK-faithful transaction-teardown failure/fallback-close tests |
| Same pagination/filter bounds and NO_RECORDS unknown | Two-page HTTP sequences, cumulative-date preflight and existing CDC/scientific regressions |
| Privacy-safe timing even on failure | Deterministic clock/span/schema tests and correlated HTTP completion tests; no county/SQL/private content in timing fields |
| No public contract/client change | Both OpenAPI exports and semantic quality/drift gates unchanged; existing compatibility suite |
| No non-CDC regression | Existing climate-session/timing, county-context, human observations and full API suite |

## Separate production acceptance after approval

Do not merge or deploy under implementation authorization. After explicit owner
approval and normal protected deployment, verify exact reviewed SHA/ACTIVE state
and startup service identity. Use the same approved #246/investigation cohorts:
01001 singleton; 01001/13081 pair; 01001/01003/01005/01007/01009 five-county batch,
both measures, geography county, start_date=end_date=2025-12-31, page_size=10.

Proposed budget at most 20 serial public requests, >=0.35s after each completion,
45s client deadline, no retries/redirects, stop on any 429, repeated 5xx or distress.
Honor Retry-After fully before any subsequent request. Begin two singleton and
two two-county samples per measure (8 requests); one five-county sample per
measure only if healthy (2); one two-page page_size=1 sequence per measure (4);
one excluded-county check per measure (2), and three representative compatibility
controls (human, climate, context), for at most 19 total. The known ingress #237
HTML 504/origin 503 on excluded counties is a separate fail-closed/transport finding;
do not count it as structured 503 success or escalate distress testing.

Validate values, NO_RECORDS unknown, approved release/revision 3/provenance,
private-field absence, no duplicates/gaps and excluded fail-closed behavior.
Capture safe request IDs, HTTP/app/component timings/counts and unavailable
fields; do not sum nested durations. Targets one <5s, two <7s, five <12s are
engineering goals, not contractual SLOs or promises. If compilation remains
dominant, report residuals and recommend separately authorized Data-owned Stage 2.
Do not alter views/warehouse, grants, rate limits or admission to meet a target.

API #248 remains open until actual production performance acceptance and its
remaining criteria are reviewed. This PR's green fixtures/CI cannot close it.

