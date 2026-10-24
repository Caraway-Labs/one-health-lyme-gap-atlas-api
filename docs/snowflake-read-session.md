# Process-scoped Snowflake read sessions (API #287)

Public read paths (`SnowflakeObservationRepository.request_session`, `SnowflakeTier1PriorityRepository`) borrow a **thread-local** Snowflake connection from `snowflake_read_session.ReadSessionPool`. This is not the Ask Atlas request-scoped write session (`docs/ask-atlas-snowflake-reuse.md`).

## Behavior

- **Scope:** one live connection per worker thread in the API process.
- **Checkout:** `read_connection(settings)` calls instrumented `dependency_telemetry.connect` only when no healthy cached connection exists for the thread.
- **Idle eviction:** default **300 s** since last successful checkout; the connection is closed and removed.
- **Health:** closed connections are replaced on the next checkout.
- **Errors:** any exception escaping `read_connection` drops and closes the thread's session so the next request reconnects.
- **CDC / close_on_exit_error:** observations CDC path may call `drop_current()` after handler errors in addition to the automatic drop on exception.

## Limits

- Do not share this pool with write paths (knowledge chat, feedback, privacy).
- Connections are not safe across threads; FastAPI sync workers must checkout on the same thread that serves the request.
- Pooling does not change SQL, OpenAPI, or public contracts.

## Operations

Tune idle TTL via `ReadSessionPool(idle_seconds=...)` only in tests; production uses the module default. Warehouse `AUTO_SUSPEND` (600 s on COMPUTE_WH) remains a separate operational knob.
