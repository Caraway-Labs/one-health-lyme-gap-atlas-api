# Ask Atlas external I/O audit (API #126)

Parent epic: #124. Measurements and hypotheses: #123. Connection ownership: #125.

Audit baseline: `b547565ab6f430ccdc144e8d184cf5539a838750`.
The literature chat route is a normal `def` path operation in `app.py`.
FastAPI dispatches it through Starlette's bounded AnyIO worker execution.
All service dependency calls are serial within that worker; converting only
the route declaration to `async def` would put blocking drivers on the event loop.

| Operation | Implementation / execution boundary | Bound and dependency |
| --- | --- | --- |
| Capability verification, budget, provenance, persistence | Synchronous Snowflake connector in route worker | Shared connector login 15s and configured network timeout; serial stored procedures; request-local connection reuse is owned by #125 |
| Neo4j readiness | Synchronous driver `verify_connectivity` in worker | 3s connection/acquisition configuration; happens before retrieval |
| Query embedding | Synchronous OpenAI client in worker; process client reuses HTTP transport | SDK retries disabled, nominal 5s timeout; required before vector retrieval |
| Neo4j retrieval | Synchronous `execute_query` in worker | Query timeout 5s; fixed hybrid template; only after embedding |
| Generation | Synchronous OpenAI Responses client in worker | SDK retries disabled; min(16s, remaining budget less reserve); only after budget admission and retrieval |
| Grounding / JSON parsing | Local CPU work in worker | Dependent on provider output; grounding unchanged; parsing nested in generation span |
| Response serialization | Framework response construction | Middleware measures through response return, not complete network delivery |
| Optional telemetry export | Shared `BatchSpanProcessor` | Export happens through processor worker; span production enqueues. No request-path force-flush found |

Chat middleware performs in-memory bookkeeping under an asyncio lock and awaits
downstream processing. No external network operation occurs inside that lock.
The existing limiter caps chat at three concurrent requests per network identity,
not a global capacity target. The framework worker limiter is also bounded;
increasing threads is not justified by two single-request samples.

## Offline evidence and result

`test_chat_io_scheduling.py` holds three fake synchronous chat workers on a
thread event, proves they run off the event-loop thread, serves a liveness
request while they remain blocked, rejects a fourth chat with 429, then releases
them and verifies limiter slots are returned. It calls no provider/database and
does not load production. Timing limits are test-hang guards, not benchmarks.

No event-loop blocking defect was identified in this route, so no driver or
async rewrite is proposed. Tests and this audit are the deliverable; they make
the existing correct boundary explicit and protect it from regression. They
do not establish a production latency or throughput gain.

## Sequencing, cancellation and unresolved constraints

Retrieval depends on embedding, generation on retrieved evidence and budget,
grounding on generation, and durable success on required final response data
and serial persistence. A single Snowflake connection must not execute overlapping
operations. Candidate-PMID provenance could only overlap generation after proving
its independence and handling unused work/errors; no such change is made here.

Cancellation of an awaiting request does not instantly stop synchronous driver
I/O. Worker execution and driver timeouts, followed by request-owned teardown,
must finish safely; abandoning a worker while sharing/closing its connection
would be unsafe. The nominal service deadline is not a hard end-to-end timeout:
post-generation database calls can exceed the five-second reserve. These remain
explicit constraints, not claims fixed by async syntax.

The current code reserves answer budget after embedding. Whether the existing
reservation contract should also admit embedding before spending needs an
explicit separately scoped decision: moving it changes no-evidence budget behavior.
For these stories the parent explicitly directed preserving current admission.
No budget boundary is silently moved by this audit. #125 preserves admission before
answer generation and does not add paid operations.

## Measurement and rollout gates

The original two samples attribute 12.812-14.956s to Snowflake-facing stages,
including 5.000-6.979s matched server CALLs and 7.812-7.977s unattributed client
remainder. Generation is 6.214-9.754s. Queue/provisioning fields were zero in
the supplied history. See #123 for limitations and conditional options.

Before further scheduling/parallel changes: obtain exact request/query mapping,
connector subspans and repeated matched safe-environment runs. Preserve budget,
grounding/citation quality, provenance/durability, errors/retries, privacy,
thread/session isolation and total cost. Any paid live QA requires a new parent
allocation; none was spent for this audit.

References: [FastAPI worker dispatch](https://fastapi.tiangolo.com/async/),
[Snowflake session reuse constraints](https://docs.snowflake.com/en/developer-guide/driver-connections).
