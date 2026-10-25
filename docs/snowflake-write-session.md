# Snowflake write connection pool (API #325)

Feedback submit/redact/export (`feedback.SnowflakeFeedbackStore`) and the Ask Atlas request-scoped session (`knowledge_chat._RequestSnowflakeSession`, #125) borrow connections from `snowflake_write_session.WriteSessionPool`. It is a different instance from the read pool and is never shared with reads. It removes the cold login (about 2.4 s measured in the chat path) from every write request.

## Rules

- **Success-only return:** a connection goes back to the pool only after the whole borrowed block finished successfully. Any exception, `KeyboardInterrupt`/cancellation, timeout or doubt closes it. The read-pool keep classifier (`discard_reason`) is deliberately not used.
- **Ask Atlas:** one connection per chat request, acquired lazily on first use on the owner thread. Teardown releases it on a clean request and discards it if any statement raised, even when the service handled the error (for example an optional provenance lookup). A closed session still raises instead of reconnecting.
- **No replay:** a procedure `CALL` is executed at most once. A failure after the request was sent surfaces as the existing `FeedbackStoreError` (feedback) or the existing chat failure paths. The pool never retries on another connection. Client retries rely on the submission token and fingerprint; the single-process feedback lock and topology gate are unchanged and atomic token idempotency is not assumed (#324 and data#782 are deferred).
- **Liveness before use only:** `is_closed()` locally; a `SELECT 1` only after `write_pool_validate_after_idle_seconds`, before any caller statement. A dead idle connection is closed and replaced.
- **Bounded:** `write_pool_max_size` (default **4**, 1-16). A caller waits at most `write_pool_checkout_timeout_seconds` (default **5 s**), then `WritePoolExhaustedError` (feedback maps it to `FeedbackStoreError`; chat takes its existing failure path).
- **Idle eviction:** connections idle longer than `write_pool_idle_seconds` (default **120 s**) are closed on the next checkout. There is no reaper thread; the FastAPI lifespan calls `close_all()` on shutdown (borrowed connections close when returned).
- **Env vars:** `WRITE_POOL_MAX_SIZE`, `WRITE_POOL_CHECKOUT_TIMEOUT_SECONDS`, `WRITE_POOL_IDLE_SECONDS`, `WRITE_POOL_VALIDATE_AFTER_IDLE_SECONDS`.
- Injected stores and standalone adapters keep their original connect-per-use lifecycle. Writes remain stored-procedure-only; no SQL, grant, schema or API payload changes.

## Measuring

Tests count logins with fake connections: before this change every feedback call and every chat request connected once; now N sequential clean calls connect once. In production, watch for fewer `atlas.dependency.snowflake.connect` spans on feedback and chat requests (and a shorter `connection_setup` span in Ask Atlas). Production latency has not been measured here.
