# API telemetry schema and operating rules

Owner: Atlas API operations; #161 under #156. Schema version: `1`.
Uses the existing shared JSON formatter and OpenTelemetry provider/exporter.
No public payload, backend, vendor, credential, sampling or retention change.
Companions: [request spans](api-request-telemetry.md), [dependency spans](
api-dependency-telemetry.md), API162's advisory `api-operational-indicators.md`,
and PR158's `ask-atlas-telemetry.md` when integrated.

## Canonical completion events

| Event | Boundary and common fields |
| --- | --- |
| `api_request_completed` | Middleware processing completes: request_id, method, path, status_code, status_class, outcome, failure_class, duration_ms, telemetry_schema_version |
| `api_request_failed` | Unhandled processing error/cancellation: same dimensions and elapsed duration; cancellation has no fabricated response status |
| `knowledge_chat_total` | One workflow completion: request_id, bounded product outcome, service_duration_ms, provider/configuration/retrieval constants; HTTP middleware adds method/path/status_code/http_status/status_class/failure_class/duration_ms |

`emit_completion` constructs events through the closed `completion_context`
schema and isolates formatter/handler failures. Unknown fields are omitted;
unknown service outcomes become `unhandled_error`. HTTP outcome is transport
classification; Ask Atlas's product outcome remains distinct. Cancellation
propagates and emits `cancelled` / unknown status class, without claiming HTTP500.
Standalone service calls have no invented HTTP fields.

Request IDs preserve existing bounded accepted `X-Request-ID` behavior and are
echoed in response headers, logs and the canonical SERVER span. They permit
direct correlation only. Do not group, label metrics, sample or shard by them.
Trace IDs belong to existing trace context, not aggregate log dimensions.
Other query/provider/response/database/user identifiers and IPs are not emitted,
even if hashed or syntactically bounded. Arbitrary configured model names are
not a closed vocabulary and are excluded.

## Bounded dimensions

`telemetry.py` owns closed `ROUTES`, `METHODS`, request outcomes/failures and
request-span attributes. ROUTES derives from committed OpenAPI paths plus fixed
health/documentation/public-detail templates and `unmatched`; new route authors
update it explicitly. An unknown template or raw path collapses to `unmatched`.
Unsupported methods become OTHER. HTTP response codes are 100–599, status class
1xx–5xx; unknown class denotes cancellation before a response.

`telemetry_logging.py` owns closed Ask Atlas outcomes, evidence states, grounding
validation reasons and stage names. Product vocabulary matches PR158, with
HTTP-only request-validation/rate-limit/route/serialization causes added for
workflow completion logs. The service span retains PR158's own bounded contract.
Generation attempts cap at 2; retrieved passage/paper counts cap at 100; durations
are nonnegative and cap at one day. Numeric measurements are not group-by fields.
Known stage latencies are kept; arbitrary stage names or diagnostic dictionaries
are dropped. Configuration/retrieval versions are source-controlled constants.

Dependency spans use `atlas.dependency.{system,operation,outcome,failure_class}`;
CLIENT timing includes only the actual I/O operation. Existing Ask Atlas stages
and `knowledge_chat.snowflake.*` retain their names. Request/service/stage/nested
dependency intervals overlap: never sum them as total wall time. Request trace
duration and middleware/service log durations describe different boundaries.

## Other established events

Stage/retrieval/grounding/provider events remain
`knowledge_chat_stage`, `knowledge_chat_retrieval`,
`knowledge_chat_grounding_rejected`, `knowledge_chat_provider_response`,
`knowledge_chat_provider_failure`, `knowledge_chat_snowflake_operation`,
`retrieval_corpus.provenance_lookup_failed`. They contain bounded outcomes,
counts, attempts, structural shape, versions and operational categories; no
retrieved/generated text, query or provider identifiers. Neo4j diagnostics use
`neo4j_connectivity_unavailable` and `knowledge_chat_serving_graph_probe` with
closed categories/probe names and structural counts. Startup census has no
request parent and does not establish request dependency availability.

Protection/cache/write events are `public_read_rejected`,
`public_read_conditional_hit`, `feedback_submission`, `feedback_topology_unsafe`.
Account failures are `account_profile_read_failed`, `account_profile_save_failed`,
`profile_store_request_failed`, `auth_admin_request_failed`,
`privacy_request_{create,confirm,status,store,processor}_failed`,
`privacy_export_download_failed`, `privacy_request_ledger_save_after_delete_failed`.
They use fixed operation/outcome/failure categories and safe upstream status,
never account identity or request data. Runtime configuration events contain
safe configuration/feature/version fields; no connection identity/host/secrets.
`atlas_readiness_check_failed` emits only a bounded dependency failure category.

Privacy resource UUIDs are not HTTP request IDs. Privacy failure events use the
active HTTP correlation context even when tracing is disabled; standalone
workflow failures use `unavailable`, never the resource identifier.

`OperationalLogger` protects all these application emitters from logging errors
and disables traceback/stack payloads. SDK log filters suppress content-bearing
INFO/DEBUG messages and convert warnings/errors to `dependency_sdk_warning` or
`telemetry_backend_warning` with bounded failure context. This intentionally
removes raw SDK debugging detail; diagnose with bounded dependency traces and
existing operator evidence. The shared formatter still owns timestamps/levels.
Uvicorn's independent stderr handlers and direct error/access emitters are
filtered too: bounded `api_server_error` / `api_server_warning` replaces raw
ASGI exception chains and request text, including when propagation to root is
disabled. API exceptions still propagate with the original response behavior.

## Privacy, quality and operations

Never log prompts/questions, bodies, answers, evidence/support quotes, auth or
capability tokens, credentials, SQL/Cypher, query arguments, URLs, unrestricted
exceptions, user identity or IP. A regex/length check or hashing alone does not
establish privacy or low cardinality. Event construction must be allowlisted;
do not merge arbitrary dictionaries into a logger call or span.

Regression evidence: `test_request_telemetry.py` checks correlation, route/status
consistency, W3C context, child parentage, concurrency, cancellation and actual
exporter failure. `test_dependency_telemetry.py` checks I/O lifecycle/failure/
redaction and real public-route nesting. `test_telemetry_governance.py` rejects
sensitive fields and arbitrary taxonomy/version/path values, SDK text and
handler failures; existing Ask Atlas tests verify sensitive evidence/answers
and provider/query/database identifiers remain absent.

Use SERVER spans for request counts, CLIENT operation spans for dependencies,
and `knowledge_chat.service` for Ask Atlas workflow statistics. HTTP500/503 alone
cannot classify infrastructure failure: use known service causes and report
unknown coverage as API162 requires. Health/docs/preflight/unmatched are separate
from the product population. Stored outcome-biased/tail-sampled traces do not
provide unbiased request counts, error ratios or latency percentiles. No data
is not evidence of health, zero latency or exporter success.

Merge after independent exact-head review, fresh-main integration and complete
quality checks. Use the existing deployment workflow and API162 natural-traffic
validation; record observed/not-observed/mock/blocked categories separately.
Do not generate paid inference to populate panels. Roll back through a reviewed
revert using the same quality/deployment path; never weaken privacy for debugging.

## Climate request timings (API #243)

The existing completion event may include `climate_timing_ms` and
`climate_operation_counts` for canonical climate observations. Keys are limited
to `service`, `release_lookup`, `measure_lookup`, `observation_query`, `mapping`,
`connect`, `execute`, `fetchone`, `fetchall`, `cursor_teardown` and
`connection_teardown`. Durations are finite nonnegative milliseconds capped at
one day; counts cap at 10000. Unknown names and values are omitted.
`climate_before_service_ms` / `climate_after_service_ms` bound the handler service
within middleware processing. They do not isolate authentication or network.
No identifiers, SQL or records are added. Existing sampling/exporter/retention
remain unchanged; this is one bounded addition to the existing event, not a
second log stream. Logical phase spans use `atlas.climate.<closed phase>`.
Intervals overlap; do not sum service, phase and I/O durations. See
[measurement boundaries and controlled acceptance](climate-http-latency.md).

## CDC request timings (API #248 Stage 1)

The existing completion event may include `cdc_timing_ms` and
`cdc_operation_counts`. Closed keys are `service`, `session_setup`,
`release_lookup`, `identity`, `authority_query`, `producer`,
`producer_processing`, `mapping`, `connect`, `execute`, `fetchone`, `fetchmany`,
`fetchall`, `cursor_teardown`, `connection_teardown` and fallback `close`. Finite durations are
nonnegative milliseconds capped at one day; counts cap at 10000. Unknown names,
nonfinite values, SQL, query IDs, county lists and private authority are excluded.
Counts include attempted operations that fail; execute counts exclude SDK-internal
session initialization and implicit COMMIT/ROLLBACK inside context teardown.

`cdc_before_service_ms` and `cdc_after_service_ms` bound the synchronous service
inside the existing middleware interval. `duration_ms` ends before final body
transport; the existing canonical SERVER span, ending at final ASGI send,
measures full server request elapsed time. Neither is public HTTP/ingress-only
latency. Middleware/service summaries are emitted even when the request trace is
sampled out. Handled 503s and unhandled failures retain completed CDC timings;
cancellation retains them only if the synchronous handler has already unwound.
Client disconnect does not forcibly interrupt a Python worker or an SDK call.

Logical spans `atlas.cdc.<closed phase>` reuse the existing provider and sampling.
`identity` and `authority_query` include execute, fetch and row decoding.
`producer` includes those serial callbacks and Data validation/mapping.
`producer_processing` is explicitly the producer interval minus the sum of its
nonoverlapping synchronous query-callback intervals; it includes local waits and
instrumentation and is **not CPU time**. API `mapping` starts after the producer
returns and includes envelope validation and DTO/context mapping. `service`
includes connection establishment through teardown. Connect includes key parsing,
authentication and session initialization; execute includes connector/network and
server waits. Teardown includes implicit transaction cleanup and closing.

Service, logical and dependency intervals overlap. Never sum their totals;
compare individual boundaries or compute interval unions. Zero rounded log
milliseconds are possible for submillisecond mock operations; missing fields
mean unavailable. No new metrics backend, diagnostic endpoint, sampling, retention
or public payload change. See [CDC session design and acceptance](cdc-request-session.md).


### CDC bounded serving selection (API #248 Stage 2)

The same completion event additionally permits `cdc_serving_path` (`batch` or
`singleton`) and `cdc_processed_counties` (bounded 1�500, including lookahead).
Existing `authority_query`, `execute`, `connect` counts and execute/fetch/producer/
mapping/service timings retain their meanings and include failed attempts.
`cdc_batch_envelope_bytes` is the UTF-8 length only when the connector returns
an already encoded JSON envelope string, capped at 8 MiB + 1 as an overflow
indicator. Native VARIANT dictionaries have no cheap encoded size; that field
is unavailable, not zero. No raw authority serialization is added for logging.
The service interval includes connection teardown; existing request duration
and SERVER span retain their documented transport boundaries. No new sampling,
private record values, SQL, county arrays or authority versions are emitted.
