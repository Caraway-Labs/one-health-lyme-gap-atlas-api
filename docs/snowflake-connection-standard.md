# Snowflake connection-handling standard (API #327, epic #316)

This is the one place that states how the Atlas API obtains, uses, returns and
discards Snowflake connections. It records what is **shipped** on `main` and labels
everything else **planned** or **deferred**. Mechanism details stay in the per-feature
documents it links; if a per-feature document and this standard disagree about the
rules below, this standard wins and the other document is a bug.

Governing decision: workspace ADR 0002 (public API and Snowflake access). The amendment
this standard proposes is drafted at the end under "Proposed ADR text" and is **not yet
accepted**; it needs owner review and placement in the workspace repository.

Related: [read pool](snowflake-read-session.md), [write pool](snowflake-write-session.md),
[readiness and monitor](snowflake-readiness.md), [pool telemetry](api-telemetry-contract.md),
[Ask Atlas request session](ask-atlas-snowflake-reuse.md),
[feedback operations](feedback-operations.md).

## 1. How a new route chooses its connection

| The route... | Use | Never |
| --- | --- | --- |
| Reads governed data to answer a public request (SELECT / read-only) | `with read_connection(settings) as connection:` from `snowflake_read_session.py` | `connect()` directly |
| Calls a stored procedure that writes, or otherwise changes durable state (feedback, Ask Atlas budget/conversation persistence, privacy linkage) | `get_write_session_pool().borrow(settings, connect)` (or `checkout`/`release`/`discard` for a request-scoped session, as `knowledge_chat.py` does) | the read pool; a retry on a second connection |
| Needs session state different from the baseline (for example a longer statement timeout) | `session_overrides(connection, statement_timeout=N)` on a `read_connection` | `ALTER SESSION` or `USE ...` statements on a pooled connection |
| Is a readiness or dependency probe, or the Snowflake monitor | its own isolated connection (see the exception list) | the pools: a probe must not consume or depend on request capacity |
| Is a standalone/batch path that must change session state outside the baseline (today only the CDC surveillance reader) | an exception in the guard allow-list, with a stated reason | adding a module to the allow-list without a reason |

Decision rule: public read -> read pool; write or procedure `CALL` that must not be
replayed -> write pool; anything else needs a recorded exception. Do not share a
connection between a read and a write path.

Every connection, pooled or not, is created by `dependency_telemetry.connect`, which
wraps the API-owned `snowflake_connection.connect` in an
`atlas.dependency.snowflake.connect` span. Construction (key-pair or PAT parameters,
login timeout 15 s, network timeout from settings, `QUERY_TAG`, explicit
`autocommit=True`, bounded parsed-private-key cache) lives in `snowflake_connection.py`.
Because the session is autocommit, code must not call `commit()` or `rollback()`
(#330, #334); each statement is its own transaction and a stored-procedure `CALL`
commits on its own.

## 2. The read pool (shipped)

`snowflake_read_session.ReadSessionPool`, one instance per process, behind
`read_connection`. Behavior, limits and environment variables are in
[snowflake-read-session.md](snowflake-read-session.md). Summary of the rules:

- Bounded (`read_pool_max_size`), with a bounded checkout wait
  (`read_pool_checkout_timeout_seconds`); exhaustion raises `AtlasDataUnavailableError`
  (the existing 503 problem response), never a hang.
- A connection is lent to one thread at a time; re-entrant use on the same thread
  shares the lease. Request statements are never retried on another connection.
- Liveness is checked only before the request's statements: `is_closed()` always,
  plus `SELECT 1` only after `read_pool_validate_after_idle_seconds` of idleness.
- Idle connections are evicted after `read_pool_idle_seconds` (on checkout and by a
  daemon reaper). The lifespan calls `close_all()` on shutdown.
- Snapshot loading (`SnowflakeAtlasRepository.load_snapshot`) and the public
  observation/metadata/provenance/Tier 1/surveillance/intelligence-feed/environmental
  reads use this pool (#317, #318, #340).

## 3. The write pool and the no-silent-replay rule (shipped)

`snowflake_write_session.WriteSessionPool` is a separate instance, never shared with
reads, used by `feedback.SnowflakeFeedbackStore` and the Ask Atlas request-scoped
session (`knowledge_chat._RequestSnowflakeSession`). Details:
[snowflake-write-session.md](snowflake-write-session.md).

Rules:

- **Success-only return.** A write connection goes back to the pool only if the whole
  borrowed block (or the whole chat request) finished without an exception. Any
  exception, cancellation, timeout or doubt closes it. The read-pool keep classifier
  is deliberately not used here.
- **No silent replay.** A procedure `CALL` is executed at most once. If the request
  may have reached Snowflake and the outcome is unknown, the failure surfaces to the
  caller (`FeedbackStoreError`, or the existing chat failure paths). Neither the pool
  nor a caller may retry it on another connection. In Ask Atlas, a closed request
  session raises instead of reconnecting, so an uncertain budget reservation or write
  is never replayed.
- Liveness before first use only (`is_closed()`, then `SELECT 1` after
  `write_pool_validate_after_idle_seconds`); a dead idle connection is replaced
  before any caller statement runs.
- Bounded (`write_pool_max_size`, checkout wait `write_pool_checkout_timeout_seconds`);
  exhaustion raises `WritePoolExhaustedError`, which callers map to their existing
  failure handling. There is no reaper thread; idle connections older than
  `write_pool_idle_seconds` are closed on the next checkout.
- Ask Atlas holds one write-pool connection per chat request, acquired lazily on the
  owner thread and released or discarded at request teardown. An owner-thread check
  rejects use from another worker.
- A connection is reused only for the identical settings object; a changed settings
  object discards the idle connection (`settings_changed`).
- The write pool does **not** apply the read session baseline (section 5).
- Injected stores and standalone adapters (for example in tests) keep their original
  connect-per-use lifecycle.

## 4. Documented exceptions to "use a pool"

`tests/test_public_reads_use_pool.py` holds the allow-list (section 9). Current entries
and why:

| Module | Why it may call `connect` directly |
| --- | --- |
| `dependency_telemetry.py`, `snowflake_connection.py` | define the instrumented entry point and the construction |
| `snowflake_read_session.py`, `snowflake_write_session.py` | the single places a pooled connection is created |
| `feedback.py`, `knowledge_chat.py` | pass `connect` into the write pool as its connect function |
| `snowflake_monitor.py` | dedicated, isolated dependency-monitor connection with monitor-only timeouts |
| `cdc_reader_probe.py`, `county_context_probe.py`, `environmental_reader_probe.py`, `model_serving_probe.py` | readiness probes on isolated connections |
| `cdc_surveillance.py` | alters session state; not yet moved onto `session_overrides` (open follow-up, not implemented) |
| `repository.py` | `SnowflakeAtlasRepository.ready()` opens a short-lived connection (see section 7); `load_snapshot` is pooled |

## 5. Session baseline and overrides (shipped, read pool)

Defined once in `snowflake_session_baseline.py` and applied by
`ReadSessionPool._create` when a connection is created (not per request):

- `ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = min(public_query_timeout_seconds, 30)`
  (the 30 s ceiling is `MAX_STATEMENT_TIMEOUT_SECONDS`).
- `USE SECONDARY ROLES NONE`: every pooled route must work with the primary role alone.
- `QUERY_TAG` is set at connect time by `snowflake_connection.py`.
- An identity check, `SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_DATABASE(),
  CURRENT_WAREHOUSE()`, compared case-insensitively with the configured user, role,
  database and warehouse. An unset setting is not asserted. A mismatch or setup failure
  closes the connection and raises `AtlasDataUnavailableError` without logging the values.

Overrides: callers must not change session state directly. `session_overrides(...)`
marks the lease dirty, applies the override, and restores the baseline in `finally`;
the lease stays dirty (and is closed at check-in) unless the restore is confirmed.
`mark_dirty()` flags a lease for the same treatment. Today `statement_timeout` is the
only supported override.

## 6. Discard policy (shipped)

Read pool, on an exception leaving `read_connection`: `discard_reason(exc, connection)`
returns `None` (check the connection in) only for a narrow set of errors where the
session is demonstrably idle and open: the API's own `PublicQueryError`, an
`AtlasDataUnavailableError` raised by API code with no chained cause, and connector
`ProgrammingError`/`DataError` for a finished statement whose errno/message does not
indicate timeout, cancellation or lost session. Everything else discards: timeouts,
cancellation and other `BaseException`s, network/auth/operational/other connector
errors, a closed connection and unknown types. When unsure, discard. Other discard
causes: failed health check, failed baseline, dirty lease, and a pool epoch change
after `close_all()`. Reasons are low-cardinality labels (section 8).

Write pool: any exception discards (`borrow_error`).

## 7. Readiness semantics (shipped)

Full text: [snowflake-readiness.md](snowflake-readiness.md).

- `GET /health/ready` reads process-local state only and performs no Snowflake I/O.
- A **startup latch**: one successful bounded startup check
  (`snowflake_startup_check_timeout_seconds`, default 10, 2-30) sets
  `startup_succeeded`. Until then readiness returns 503. The latch never un-sets.
- After the latch, readiness stays 200 and reports
  `{"status":"ready","dependencies":{"snowflake":"ok"|"degraded"}}`. The field is
  informational, sampled by the monitor; it is not a per-request availability guarantee.
  Data routes keep their own 503 behavior.
- A background daemon **monitor** (`snowflake_monitor.SnowflakeMonitor`) checks every
  `snowflake_monitor_interval_seconds` (default 60). Checks never overlap, are bounded
  by a monotonic deadline (3 s after startup), and emit only the transition logs
  `snowflake_dependency_degraded` / `snowflake_dependency_recovered` plus an
  `atlas.dependency.snowflake.check` span.
- Wiring: in production (`create_app` called without an injected repository) the monitor has no
  probe, so its own worker thread owns one long-lived connection built by
  `snowflake_connection.monitor_connection_parameters` (monitor-only timeouts, no keep-alive) and
  reuses it across checks, with at most one reconnect when the session fails. Only when a test
  injects a repository does `create_app` pass `AtlasService.ready` as the probe
  (`app.py`: `SnowflakeMonitor(config, service.ready if repository is not None else None)`).
  Neither mode uses the read or write pool, which is the rule that matters: readiness must not
  consume request capacity. See [snowflake-readiness.md](snowflake-readiness.md).
- The feedback process-topology gate and the enabled-chat wiring gate still apply to
  readiness.

## 8. Snapshot stale-serve rule (shipped)

`AtlasService` caches one release snapshot for `cache_ttl_seconds` (default 300). When a
refresh fails it keeps serving the previous snapshot for at most
`snapshot_max_stale_seconds` past the TTL (default 900; 0 disables stale serving). After
that bound it raises the existing 503 (`AtlasDataUnavailableError`). Refreshes are
single-flight, and a failed refresh backs off for `snapshot_refresh_backoff_seconds`
(default 30) so an outage is not retried per request. With no snapshot ever loaded there
is nothing to serve: the 503 is returned.

What is **not** exposed publicly: staleness is not added to any response body, header or
OpenAPI schema. The snapshot's `release_id`, `generated_at` and `last_updated` are never
rewritten, so provenance continues to describe the data actually served. The only HTTP
effect is that, while the snapshot is past its TTL, a `public` `Cache-Control` is replaced
with `private, no-store` so intermediaries never cache stale data as fresh. Operators see
`atlas_snapshot_refresh_degraded` and `atlas_snapshot_refresh_recovered` logs. Any change
that exposes staleness to API clients is a public-API change and needs its own decision
and OpenAPI/contract update.

## 9. Enforcement: the AST guard (shipped)

`tests/test_public_reads_use_pool.py` parses every module in `src/lyme_gap_atlas_api/`
and fails if a module not in `ALLOWED_DIRECT_CONNECT` contains
`from ... import connect`. A second test fails if the allow-list names a module that no
longer exists. A third test proves repeated public reads share one login. Adding a
module to the allow-list requires a reason in the dict value and a matching row in
section 4. Note the guard checks imports of the name `connect`; it does not prove a
route chose the right pool, so review still checks section 1.

## 10. Settings and environment variables

Defaults are the field defaults in `config.py`.

| Environment variable | Default | Range | Purpose |
| --- | --- | --- | --- |
| `READ_POOL_MAX_SIZE` | 16 | 1-64 | live read connections per process |
| `READ_POOL_CHECKOUT_TIMEOUT_SECONDS` | 5.0 | >0-30 | max wait for a read connection |
| `READ_POOL_IDLE_SECONDS` | 300.0 | >0-3600 | idle eviction age |
| `READ_POOL_VALIDATE_AFTER_IDLE_SECONDS` | 30.0 | 0-3600 | idle age after which `SELECT 1` runs at checkout |
| `WRITE_POOL_MAX_SIZE` | 4 | 1-16 | live write connections per process |
| `WRITE_POOL_CHECKOUT_TIMEOUT_SECONDS` | 5.0 | >0-30 | max wait for a write connection |
| `WRITE_POOL_IDLE_SECONDS` | 120.0 | >0-3600 | idle eviction age |
| `WRITE_POOL_VALIDATE_AFTER_IDLE_SECONDS` | 30.0 | 0-3600 | idle age after which `SELECT 1` runs at checkout |
| `SNOWFLAKE_MONITOR_INTERVAL_SECONDS` | 60 | 1-3600 | monitor cadence |
| `SNOWFLAKE_STARTUP_CHECK_TIMEOUT_SECONDS` | 10 | 2-30 | startup latch budget |
| `CACHE_TTL_SECONDS` | 300 | 30-3600 | snapshot TTL |
| `SNAPSHOT_MAX_STALE_SECONDS` | 900 | 0-86400 | stale-serve window past TTL |
| `SNAPSHOT_REFRESH_BACKOFF_SECONDS` | 30 | 1-3600 | back-off after a failed refresh |
| `PUBLIC_QUERY_TIMEOUT_SECONDS` | 15 | 1-60 | public query timeout; the baseline statement timeout is `min(this, 30)` |

Fixed in code, not environment-tunable: connector login timeout 15 s
(`LOGIN_TIMEOUT_SECONDS`), checkout `SELECT 1` timeout 5 s, reaper interval 60 s,
read-pool statement-timeout ceiling 30 s.

## 11. Observability signals (shipped)

Contract and redaction rules: [api-telemetry-contract.md](api-telemetry-contract.md),
section "Snowflake pool telemetry". Both pools emit the span event
`atlas.snowflake.pool.checkout` (outcome `reused`, `created`, `replaced_dead` or
`exhausted`, wait, in-use, idle, max), `atlas.snowflake.pool.discard` with the low-
cardinality reason, `atlas.snowflake.pool.idle_eviction`, and the logs
`snowflake_pool_discard`, `snowflake_pool_idle_eviction` and `snowflake_pool_exhausted`.
Login latency is the existing `atlas.dependency.snowflake.connect` span; a login happens
exactly when the checkout outcome is `created` or `replaced_dead`. `pool_stats()` is an
in-process diagnostic snapshot, not served by any endpoint. Nothing here carries SQL,
credentials, identity values, exception messages or user data. The repeatable fake-based
comparison is `scripts/benchmark_snowflake_connections.py`; it is not a production
measurement.

## 12. Deferred and planned (NOT implemented)

- **Atomic feedback token idempotency (API #324, data #782).** Feedback submission
  uniqueness still depends on a process-local lock stripe pool and on running exactly one
  API process. The single-process gate (`tests/test_deployment_config.py`: one
  `instance_count`, at most one uvicorn worker; readiness fails closed otherwise) is
  unchanged. Do not remove it until database-side atomic token idempotency lands.
- **Related single-instance assumptions** remain: the process-local public and chat rate
  limiters, the per-process pagination token signing secret, and the
  `instance_count: 1` deployment gate. Pooling does not make the API multi-instance safe.
- `cdc_surveillance.py` still opens its own connection and alters session state (section 4).
- The shared-python package is no longer used for connection construction in `src/`, but
  settings (`SnowflakeSettings`) are still imported from it, and the data repository
  still uses the shared connector; see [api-layer-inventory.md](api-layer-inventory.md).
- Production latency and login-rate improvements from the pools are not established by
  local tests; validation after deployment is tracked in the epic.

## Proposed ADR text

> **REQUIRES OWNER REVIEW.** This section is a draft addendum to workspace ADR 0002. The
> API repository does not own that ADR. The text below must be reviewed by the decision
> owner and, if accepted, placed in the workspace repository under `docs/adr/` (either as
> an addendum to 0002 or as a new ADR that supersedes part of it). Nothing here has been
> accepted, and no file in the workspace repository was changed by this work.

### ADR 0002 addendum: Snowflake connection handling in the Atlas API

Status: **Proposed**
Decision owner: One Health Lyme Gap Atlas product and engineering leads

#### Context

ADR 0002 fixes the access boundary (the API is the only Snowflake client, using a
least-privilege service identity) but is silent on connection lifecycle. The API
previously logged in per read and per request, held connections for the life of a
request, carried a shared connector module, and documented its behavior across
per-feature notes. Epic #316 introduced bounded pools, a session baseline, a readiness
monitor and a stale-serve rule. The behavior needs one recorded decision so that new
routes follow it by default.

#### Decision

1. The API owns Snowflake connection construction; every connection is created through
   one instrumented entry point, with explicit autocommit. No commit/rollback calls.
2. Public read paths borrow from a bounded per-process read pool with bounded checkout
   wait; exhaustion is a typed 503, never a hang.
3. Write paths and stored-procedure `CALL`s use a separate bounded write pool. A write
   connection is returned only after full success. A write or procedure call is never
   retried or replayed on another connection (no silent replay).
4. Pooled read connections carry a single session baseline (statement timeout capped at
   30 s, no secondary roles, query tag, verified service identity). Session state changes
   only through a restoring override mechanism; unconfirmed restores discard the connection.
5. Connections are discarded on any error not classified as benign; when unsure, discard.
6. Readiness is a startup latch plus a background monitor that does not use the pools;
   later outages degrade an informational field, not readiness.
7. When a snapshot refresh fails, the previous snapshot may be served for a bounded
   window past its TTL, with provenance unmodified, staleness not exposed in public
   payloads, and public caching withheld.
8. Direct `connect` use outside the pools is limited to an explicit, reasoned allow-list
   enforced by an automated test.
9. The API remains a single-process, single-instance deployment for feedback idempotency
   until atomic database-side token idempotency (API #324, data #782) is delivered.

#### Consequences

- Fewer logins and lower, bounded Snowflake concurrency per process; stable identity and
  session state for pooled reads.
- Capacity is per process, so scaling out would multiply the Snowflake connection
  ceiling and is blocked anyway by decision 9.
- Stale data may be served for up to the stale window during an outage; this is a
  deliberate availability trade-off, limited and not advertised in payloads, so clients
  cannot yet tell stale from fresh except through cache headers and operator logs.
- New routes must pick a pool or add a justified exception; the guard test is the
  enforcement point and does not by itself prove the correct pool was chosen.
- Tuning knobs and telemetry are specified in the API repository's
  `docs/snowflake-connection-standard.md` and `docs/api-telemetry-contract.md`.

#### Status

Proposed. Not accepted until reviewed by the decision owner and placed in the workspace
repository `docs/adr/`.
