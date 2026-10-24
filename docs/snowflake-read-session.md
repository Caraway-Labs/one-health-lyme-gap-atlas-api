# Bounded Snowflake read connection pool (API #287, #317, #318, #319)

Every public read path borrows (observations, Tier 1, metadata, provenance, surveillance situations, intelligence feed, `SnowflakeAtlasRepository.load_snapshot`, and the `_cursor()` fallbacks in observations/environmental context; API #318) borrow a connection from the process-wide `snowflake_read_session.ReadSessionPool`. This is not the Ask Atlas request-scoped write session (`docs/ask-atlas-snowflake-reuse.md`).

## Behavior

- **Bounded size:** at most `read_pool_max_size` (default **16**, 1-64; previous effective cap was the anyio threadpool, about 40) live connections per process, independent of the anyio threadpool size.
- **Checkout queue:** a request waits at most `read_pool_checkout_timeout_seconds` (default **5 s**) for a free connection, then raises `AtlasDataUnavailableError` (the existing 503 problem response). It never hangs.
- **One borrower at a time:** the connector is thread-confined, so a connection is lent to a single request/thread until it is returned; re-entrant use on the same thread shares the lease.
- **Check-in:** `read_connection` returns the connection on a successful exit. Any exception closes it (`drop_current`) and frees the slot; which errors should merely return the connection is a later refinement (#320).
- **Session baseline (#319):** defined once in `snowflake_session_baseline.py` and applied by `ReadSessionPool._create` when a connection is created (one cursor, once per connection, never per request): `STATEMENT_TIMEOUT_IN_SECONDS = min(public_query_timeout_seconds, 30)`, `USE SECONDARY ROLES NONE` (so every pooled route must work on the primary role alone), and the `QUERY_TAG` set at connect. The same cursor then runs `SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_DATABASE(), CURRENT_WAREHOUSE()` and compares it, case-insensitively, with the configured user/role/database/warehouse (an unset value is not asserted). A failure closes the connection and raises `AtlasDataUnavailableError` without logging the values.
- **Overrides:** callers must not change session state directly. Use `with session_overrides(connection, statement_timeout=N):`; it restores the baseline in `finally`. The lease is *dirty* from the override until the restore is confirmed, and a dirty lease (or one flagged with `mark_dirty()`) is closed at check-in instead of reused. The standalone CDC `_read` path (not pooled) still runs its own `_setup_session`.
- **Health on checkout:** `is_closed()` first (local, free). Only when the connection has been idle longer than `read_pool_validate_after_idle_seconds` (default **30 s**) is one `SELECT 1` sent. A failing connection is closed and replaced before the request's statements run; request statements are never retried.
- **Idle eviction:** connections idle longer than `read_pool_idle_seconds` (default **300 s**) are closed on checkout and by a daemon reaper thread (every 60 s).
- **Shutdown:** the FastAPI lifespan calls `close_all()`: idle connections close immediately, borrowed ones are closed when returned.
- **Keep-alive:** the connector's `client_session_keep_alive` is evaluated and left off: it adds a heartbeat thread and traffic per pooled connection, and the idle-validated checkout plus idle TTL already replace dead sessions before use.
- **Env vars:** `READ_POOL_MAX_SIZE`, `READ_POOL_CHECKOUT_TIMEOUT_SECONDS`, `READ_POOL_IDLE_SECONDS`, `READ_POOL_VALIDATE_AFTER_IDLE_SECONDS`.
- **CDC / close_on_exit_error:** observations CDC path may call `drop_current()` after handler errors in addition to the automatic drop on exception; `drop_current()` is idempotent.

## County context reads (API #288)

Successful `CONTEXT_FIELDS` observation searches (`population_2022`, etc.) use **one** `execute` that resolves `CURRENT_RELEASE_V` and fetches rows in a single statement. When that statement returns a release but no observation rows, the API runs **`measure_exists`** once to distinguish measure-not-found from selection-not-admitted.

`api_request_completed` for these routes includes `context_timing_ms` / `context_operation_counts`. Snowflake failures log `context_observation_query_failed` with `snowflake_error_type` only (no SQL text).

## Direct `connect()` exceptions (API #318)

Public read modules must call `read_connection(settings)`, never `connect()`; `tests/test_public_reads_use_pool.py` fails on a direct `connect` import outside an explicit allow-list: the probes (isolated readiness connections), `feedback.py` (writes, #325), `knowledge_chat.py` (request-scoped chat session), `snowflake_monitor.py`, `cdc_surveillance.py` (alters session state; follow-up), and `SnowflakeAtlasRepository.ready()` (no longer used by readiness since #310).

## Limits

- Do not share this pool with write paths (knowledge chat, feedback, privacy).
- Connections are not safe across threads; FastAPI sync workers must checkout on the same thread that serves the request.
- Pooling does not change SQL, OpenAPI, or public contracts.

## Operations

Tune the pool with the settings above. Warehouse `AUTO_SUSPEND` (600 s on COMPUTE_WH) remains a separate operational knob. A single place creates connections (`ReadSessionPool._create`) and a single path returns them (`checkin`), so session baseline, state reset and pool metrics hook in there.
