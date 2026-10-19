# API #265: bounded Neo4j retrieval remediation

This implementation follows [#265](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/265)
and the diagnostic report in [PR #266](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/pull/266).
No production deployment is authorized or performed. The original production
measurement was 120,930 ms inside retrieval, out of 122,021 ms at the API.
Its exact socket/server/retry split remains unknown.

## Change and cancellation ownership

The synchronous FastAPI handler and KnowledgeChatService remain. The service
publishes its remaining budget as an absolute `time.monotonic()` deadline in
a request-local ContextVar. After the existing embedding stage, Neo4j gets the
earlier of that deadline and **now + 5 seconds**. Dispatch time is included;
the cap is not restarted for each phase or retry. An already expired budget
does not open a driver.

Only retrieval crosses AnyIO's supported `from_thread.run` bridge from the
existing FastAPI worker to its existing asyncio event loop. The worker waits
for completion, including cancellation cleanup. No thread/future timeout,
new event-loop thread, shielded query, detached query task or executor for
retrieval is introduced. AnyIO was already locked transitively; it is now an
explicit dependency, with no version change.

On that loop a request-scoped **Neo4j 6.2.0 AsyncDriver** runs the same fixed,
parameterized HYBRID_SEARCH in one explicit read transaction. Eager result
consumption, commit, session exit and driver exit are all inside
`asyncio.timeout_at`. The scoped pool has one connection and is closed before
the retrieval returns. The existing synchronous driver is retained only for
the existing readiness/startup diagnostics, outside the request retrieval path.
There is no cross-request async pool that can retain a stalled socket.

The source and fault tests establish why this is different from wrapping a
synchronous call in a timer:

- The old `Driver.execute_query` enters the managed transaction retry loop.
  `Query(timeout=5)` supplies a **server transaction timeout**, not a transport
  read deadline. The synchronous service checked its 28-second deadline only
  after retrieval returned. Acquisition/connect caps did not cover the entire
  result lifecycle.
- In the pinned async driver's `AsyncSession.__aexit__`, `_connect`, and
  transaction cancellation handlers, cancellation kills/releases the affected
  connection. `AsyncIOPool._close_connections` kills the remaining connections
  if graceful pool close is cancelled. These are inspected pinned-source
  behaviors, also exercised by actual socket EOF assertions in tests.
- Retrieval uses `begin_transaction` and `tx.run`, **not** `execute_query`,
  `execute_read` or implicit autocommit. It therefore has no managed retry or
  backoff. A transient server failure executes only one BEGIN. Setting the
  managed retry budget to zero alone was rejected: on a coarse monotonic clock
  the pinned loop can still enter its first retry/backoff.
- The outer timeout cancels the coroutine performing actual I/O in the same
  task. Supported async context managers unwind before the bridge returns.
  The timer covers normal cleanup, error/reset cleanup and connection opening,
  rather than timing only RUN or only the server query.

Timeouts retain the existing HTTP **503**, `Retry-After: 30`, and typed
`evidence_unavailable` response with no claims/citations. Existing telemetry
records `neo4j_timeout` and `dependency_failure`; known server transaction
timeout codes use the existing closed code classifier. Other graph failures
remain `neo4j_query_failure`. No new telemetry fields, prompts, query text,
credentials, raw exceptions or evidence content are emitted. Grounding,
retrieval provenance, Snowflake, model generation and public schemas are unchanged.

## Effective bounds

| Boundary | Value / ownership |
| --- | --- |
| Retrieval total | min(5000 ms, remaining service budget), including dispatch, acquisition, I/O, result consumption, commit and cleanup |
| Neo4j acquisition/connect/write | 3000 ms each, subordinate to the outer absolute retrieval deadline |
| Server transaction | min(5000 ms, remaining retrieval budget at entry); an additional server guard, not the cancellation mechanism |
| Transaction attempts | One explicit transaction, no application/managed retry |
| Pool | One per request, maximum one connection, disposed at exit |
| Embedding/generation/Snowflake | Existing limits; not changed by this fix. This does not make the entire Ask Atlas workflow a hard 28-second deadline. |

The async timeout requires a responsive event loop and cancellable driver I/O;
it is not a real-time OS scheduling guarantee or protection against synchronous
CPU starvation. Result count remains bounded by the existing query. Connection
closure cannot promise instantaneous termination of server-side work; retain
the server transaction timeout as an independent guard.

The inspected live URI is `bolt` with a numeric address (no address or credential
published). Python/Neo4j may use the existing event loop's bounded DNS executor;
this implementation creates no per-request resolver executor. Cancellation stops
awaiting hostname resolution but cannot forcibly kill an OS resolver syscall.
Numeric-address production configuration avoids remote DNS resolution. A future
hostname/routing configuration needs resolver-stall/resource verification before
promotion; the tests do not claim to kill an arbitrarily hung OS DNS thread.

## Reproducible measurements and checks

Run from the isolated checkout:

```powershell
uv sync --extra dev --locked
uv run pytest -q -s -o addopts='' tests/test_neo4j_retrieval_deadline.py
uv run pytest -q -o addopts='' tests/test_knowledge_chat_latency.py tests/test_neo4j_readiness_diagnostics.py tests/test_knowledge_chat_diagnostics.py tests/test_request_telemetry.py tests/test_chat_io_scheduling.py
uv run ruff check .
uv run mypy
uv run pytest -q
uv run python scripts/export_openapi.py
uv run python scripts/openapi_quality.py --base-ref origin/main
docker build -t atlas-api-265-bounded-retrieval .
```

Tests use the actual pinned client against a local minimal Bolt peer, not a
mock that merely raises TimeoutError. The peer has a 125-second no-reply limit;
client cancellation must close its socket first. The fault cap is accelerated
to 100 ms and checked against a 1000 ms scheduling ceiling (not a production
SLO). Normal records and failed connection behavior are also exercised.

| Local test / observation | Measured result from focused execution |
| --- | --- |
| No response during handshake / BEGIN / RUN / PULL / COMMIT | Approximately 94–125 ms with a 100 ms cap; socket EOF and no retained session tasks required |
| Same peer delays PULL by 350 ms: previous synchronous execute_query | 375 ms, despite its nominal server query timeout |
| Same peer: bounded async transaction | 110 ms with 100 ms cap |
| Transient failure | Exactly one BEGIN, no managed retry |
| Pool-close stall / failed connection / reset stall | Elapsed bound and cleanup asserted, not just exception type |
| Four sequential and six concurrent no-reply requests | All sockets close; no retained driver tasks; resolver executor threads reclaimed when test loop exits |
| Real retriever through FastAPI | Typed 503, `neo4j_timeout` completion, no generation, empty claims |

Fixture results do not prove deployed behavior, answer accuracy on the live
corpus or live platform ingress passthrough. Full-suite, build and hosted quality
results are recorded on the implementation PR after completion.

## Separate ingress observation

A read-only GET of the existing unpublished county geometry route returned
external **504 text/html in 370 ms** with **`x-do-orig-status: 503`**, and echoed
the synthetic diagnostic request ID. This freshly corroborates App Platform
substitution of an origin 503; it is not a 370 ms application timeout. The
original Ask Atlas screenshots alone did not establish the origin error cause.
The exact internal proxy rule and supported bypass remain unverified. This PR
preserves truthful 503 semantics; it does not disguise errors as successful
answers or modify proxy/UI configuration. Infrastructure can still replace
the controlled response body, but retrieval will stop before the previous
two-minute interval under the tested failure modes.

## Review and deployment recommendation

Owning contracts: `ask-atlas-operations.md`, `ask-atlas-telemetry.md`,
`api-telemetry-contract.md`; accepted KG ADR 0007 remains unchanged. This is an
internal I/O implementation change on the same API/runtime/Neo4j topology,
without an API shape, data classification or access-model change.

Recommend merge only after independent procedural review, green exact-head
quality, and confirmation that the runtime URI remains the inspected direct
numeric-address Bolt configuration. Main merge triggers the existing production
workflow, so **human merge authority is also the deployment decision**. No
automatic merge or deployment was requested/performed. After authorized rollout,
confirm exact SHA/ACTIVE status, a bounded controlled retrieval failure in an
approved environment, ordinary live retrieval/grounding, safe completion logs,
and ingress behavior separately. Revert this narrowly scoped change through the
normal reviewed deployment path if cancellation or normal retrieval regresses.

Branch: `codex/api-265-bounded-retrieval`; base `d0472707cfc2badae714a797ec54c83d4f7ad79b`.
Worktree: `C:\codex_programming\api-265-bounded-retrieval`.
PR #266's diagnostic worktree and every other checkout remain untouched.
