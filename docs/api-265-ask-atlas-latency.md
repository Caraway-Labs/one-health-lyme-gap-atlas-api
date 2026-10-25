# API #265: production Ask Atlas latency investigation

Inspection: 2026-10-18, approximately 23:09–23:16 UTC. Diagnosis only; no
production configuration, deployment, database administration, or code change.
Owning contracts: `api-telemetry-contract.md`, `ask-atlas-telemetry.md`,
`ask-atlas-operations.md`; accepted KG ADR 0007. Existing endpoint behavior,
privacy, grounding and provenance boundaries are preserved.

## Finding

There are two distinct failures behind the visible “timeout”:

1. A natural request starting 23:05:21 UTC spent **120,930 ms in the Neo4j
   retrieval SDK boundary**, out of **122,021 ms API elapsed** (99.1%). It
   completed retrieval successfully, then failed the service deadline check.
   Embedding took 1,058 ms; Snowflake and generation were never reached.
   This is consistent with the supplied approximately two-minute browser wait,
   but the screenshot has no request ID, so exact incident correlation is unproven.
2. Two controlled literature requests completed at the API in 18,712 and
   13,116 ms with `corrective_retry_exhausted`: both generated twice and failed
   the closed grounding rule that claim text must be supported by the quote.
   The API recorded **503**, but the public client received **504 HTML** with
   the same diagnostic request ID. These are not measured deadline expirations.
   Intermediary status/body replacement is observed; its exact platform rule
   and internal hop remain unverified. A 504 alone cannot identify timeout origin.

The largest observed delay belongs to retrieval, not Snowflake or the model.
The precise split inside that 120,930 ms (pool, routing, socket, server work,
managed-transaction retries, record transfer) is **unmeasured**. Do not call it
120 seconds of Cypher execution or assert a stale connection as proven cause.

## Procedure and individual requests

First inspect existing safe App Platform completion/stage events and Tempo.
Use a six-hour UTC trace window and a normalized chat-route filter, then
`{ name = "knowledge_chat.service" }`; check a general trace search separately
to establish backend query health. Use the existing authenticated SSH path to
the observability host and its internal Tempo API; no public ports or credentials
were created. Exact natural request IDs stay out of this report.

Send only three sequential requests to the existing public chat endpoint, using
empty history, controlled public fixture categories below, JSON content type,
and a new `X-Request-ID` prefixed `api265-`. Use a 145,000 ms client timeout,
zero client retries, and a monotonic clock before opening the request, at response
headers, and after reading the body. Discard bodies after extracting only status;
never save prompts, answers or returned conversation capabilities. Search
`knowledge_chat_total`, `knowledge_chat_stage` and
`knowledge_chat_snowflake_operation` by the synthetic ID. No direct Snowflake
query or configuration change is needed. Ordinary endpoint budget/persistence
procedures run as part of the authorized reproductions.

| Run/category; UTC start | Public / API status | Client total / TTFB / download ms | API / service ms | Request correlation; trace |
| --- | --- | --- | --- | --- |
| R1 public tick surveillance; 23:11:40.973 | 504 HTML / 503 validation failure | 19043.04 / 19041.68 / 1.36 | 18712 / 18704 | `api265-0f6df458f0924f21be89dbbf5e14ee8e`; no retained trace found |
| R2 surveillance limitations; 23:12:00.019 | 504 HTML / 503 validation failure | 13303.93 / 13303.55 / 0.38 | 13116 / 13108 | `api265-5d71402327f1411fa0350b9c9971cd7c`; no retained trace found |
| R3 personal-diagnosis safety control; 23:12:13.326 | 200 / 200 safety refusal | 8150.49 / 8149.85 / 0.64 | 7955 / 7949 | `api265-b8f94e25594c49b3906379f95834f95a`; no retained trace found |
| N1 natural incident; 23:05:21 | Browser 504 not exactly correlated / API 503 deadline exhausted | Screenshot approximately 120000; exact client unavailable | 122021 / 122014 | Restricted natural ID; no retained trace found |

R3 proves successful transport and safe abstention, **not a successful literature
answer**. No answered literature response was observed; no further paid traffic
was generated to manufacture one. Three controlled requests, no client retries;
R1/R2 each made two generation attempts (one corrective retry). Neo4j's internal
transaction-attempt count is unavailable. N1 made zero generation attempts.

| Nonoverlapping stage / nested operation (ms) | R1 | R2 | R3 | N1 |
| --- | ---: | ---: | ---: | ---: |
| Embedding | 754 | 152 | Not reached | 1058 |
| Neo4j retrieval | 31 | 42 | Not reached | 120930 |
| Budget stage, including connection and procedure | 4241 | 3549 | Not reached | Not reached |
| ↳ Snowflake connection setup | 2299 | 2348 | 2417 within persistence | Not reached |
| ↳ Budget execute / fetch | 1940 / 0 | 1199 / 0 | Not reached | Not reached |
| Generation attempt 1 / 2 | 6792 / 6766 | 5082 / 4176 | Not reached | Not reached |
| Grounding validation total | 0 | 0 | Not reached | Not reached |
| Conversation persistence | Not reached | Not reached | 7859 | Not reached |
| ↳ Persistence executes | Not reached | Not reached | 3227 + 2213 | Not reached |
| Snowflake close | 116 | 97 | 89 | Not reached |
| Service residual after outer stages and close | 4 | 10 | 1 | 26 |
| Middleware minus service (not isolated auth/queue) | 8 | 8 | 6 | 7 |
| Client minus middleware (combined transport/edge residual) | 331.04 | 187.93 | 195.49 | Unavailable |

Safety classification rounded to 0 ms. Zero is rounded, not proof of no work.
Nested rows are not added again. Calls run serially on a synchronous worker;
generated JSON parsing is nested inside generation, not separately timed here.
Residual is arithmetic on rounded log durations, not CPU time. It includes
untraced application work and rounding. DNS/TCP/TLS, ingress queue, worker queue,
auth, serialization and response buffering cannot be separated with these logs.
The request has no bearer or conversation capability; existing-conversation
authorization is not exercised. No streaming, hence no time-to-first-token.

For the **three controlled runs only**, client p50/max = 13303.93/19043.04 ms;
API p50/max = 13116/18712 ms. This mixed-outcome N=3 sample is not a production
percentile. N1 is historical and excluded. Existing natural examples at
22:36:57 and 22:37:41 completed in 23144 and 12978 ms, respectively: generation
timeout after a rejected first answer, and corrective retry exhaustion. Natural
aggregate chat population percentiles were unavailable, not zero.

## Timeout and lifecycle inventory

Production API deployment `82915c2d-4291-4626-879c-29e7b40292b8` was ACTIVE at
`d0472707cfc2badae714a797ec54c83d4f7ad79b`, matching the investigation base.
Live configuration confirms chat enabled and OTel endpoint/header configured
without exposing values. No chat timeout override keys were present in the
inspected service env list; values below are deployed-source defaults unless
explicitly described otherwise (inherited app-level overrides not established).

| Layer | Verified source/configuration and implication |
| --- | --- |
| Browser | Web main `95de86e6398b70f9d48f0f19d2c39a5dd25ecf00`: `use-evidence-chat.ts` supplies cancellation via AbortController, no elapsed deadline; `api-mutator.ts` uses fetch without an added timer/retry. This is source evidence, not verified deployed Web SHA. |
| Diagnostic client | urllib, 145000 ms timeout, no configured proxy, no application retry. No controlled request hit this timeout. |
| Edge/App Platform | Actual client 504 HTML versus correlated API 503; screenshot roughly 120000 ms. Exact edge deadline/config and replacing hop unavailable; no app-spec request-timeout override found. Do not infer a 19-second configured cutoff from R1. |
| API workflow | 28000 ms cooperative service deadline; checked after retrieval/budget and before generation, not an interrupt of blocking retrieval. N1 proves this is not a hard wall-clock bound. |
| Neo4j | Long-lived shared driver/pool; connection acquisition and connect each 3000 ms. `execute_query(Query(..., timeout=5))`: 5000 ms transaction timeout; SDK managed transaction may retry. Locked driver 6.2.0 default retry budget 30000 ms, liveness check unset; retry budget is not a whole-call hard deadline. Per-attempt counts/socket and query-server durations unavailable. No explicit timeout/keepalive overrides in inspected Neo4j config. |
| OpenAI | Embedding timeout 5000 ms, max_retries=0. Generation at most 16000 ms, shortened to remaining service budget with 5000 ms post-generation reserve; max_retries=0, at most two application generation attempts, 2048 output tokens. |
| Snowflake | One lazy exclusive connection per request, reused for its operations, closed at service exit; no cross-request pool. Shared helper login timeout 15000 ms, network timeout setting default 600000 ms. Actual runtime network override and statement timeout not independently verified. Execute includes connector/network/server wait, not pure SQL execution. |
| Platform health | Committed readiness/liveness probe timeout 5000 ms; this is not the chat HTTP deadline. |

No Snowflake metadata query was warranted for N1: it never reached Snowflake.
Other runs show connection setup separately from execute. Server queue,
compilation/execution splits remain unavailable because current safe telemetry
deliberately omits query IDs; do not equate execute wall time with warehouse CPU.

## Ranked improvements (hypotheses for separately reviewed fixes)

1. **Bound retrieval across its complete lifecycle.** Observed impact: 120930 ms,
   versus 31–78 ms retrieval in other inspected requests. Minimum next fix:
   inspect/test the locked driver deadline behavior across stale socket, pool,
   transaction retry and slow server cases; enforce the remaining request budget
   over the blocking call, including retry/transport handling. Merely checking
   the clock after retrieval or changing Query timeout is insufficient. Hypothesis:
   bounded retrieval prevents the two-minute stall and returns a typed failure
   before ingress expiry. Preserve cancellation safety; do not launch detached
   unbounded workers or weaken evidence checks. Stale pooled connections/retries
   are candidates, not confirmed root causes. A later idle Neo4j host snapshot
   showed low load and ample available memory; it does not prove incident load.
2. **Investigate generation/grounding compatibility using selected deidentified
   fixtures.** R1 spent 13558 ms and R2 9258 ms in generation, with both attempts
   rejected. Their corrective retries added 6766/4176 ms without an answer.
   Hypothesis: the smallest prompt/output-format correction could reduce rejected
   retries; validate against the existing grounding/eval gate. Never relax quote
   support validation to improve apparent success or latency. Separately correct
   the timeout-only UI classification after verifying the edge's 503 replacement.
3. **Review Snowflake procedure overhead only after the above.** Connection setup
   2299–2417 ms, budget execute 1199–1940 ms, safety persistence executes 5440 ms.
   Hypothesis: a narrowly governed procedure/session improvement may save seconds
   on the ordinary path. Existing request reuse already removes repeated setup;
   no evidence justifies a new cross-request pool or warehouse changes now.

## Telemetry gaps and checks

Reused App Platform safe JSON events and the existing Tempo/collector stack.
Six-hour route/service searches returned no matching chat traces. General search
succeeded and returned retained API health traces (latest inspected at 22:58:34
UTC), confirming query access, not current chat export. Live collector config:
5000 ms tail decision wait, ERROR retention plus probabilistic normal sampling,
5000 ms batch timeout. No collector log lines in the inspected preceding two
hours. Missing chat traces may reflect early sampling decisions, late spans,
export delay/loss or filters; cause and current deployed sampling percentage
remain unverified. No chat trace ID can honestly be supplied. No new instrumentation
was added: existing logs identify stage dominance and independent failure causes.
Minimum remaining evidence is one retained slow chat trace with safe Neo4j
attempt/acquisition/server timing, or a controlled local locked-driver failure
fixture, before selecting a transport-specific fix. Do not change sampling or
enable raw SDK query/prompt logging as part of this investigation.

Focused existing regression checks: 65 passed across
`test_knowledge_chat_latency.py`, `test_chat_io_scheduling.py`,
`test_chat_snowflake_reuse.py`, `test_request_telemetry.py`. Fixture success
does not establish production timeout enforcement. Full quality results are
recorded in the PR. Only documentation is proposed for review.

Isolation retained: branch `codex/api-265-ask-atlas-timeouts`, worktree
`C:\codex_programming\api-265-ask-atlas-timeouts`. No other worktree was switched,
reset, cleaned or edited. Sanitized local diagnostic artifacts remain in this
worktree's ignored `.diagnostics/`; no prompt/response bodies are retained there.
Acceptance is qualified: three runs and log correlation delivered; successful
literature answer, matching retained trace, fine-grained retrieval attribution
and exact edge deadline remain unobserved. No deployment or issue closure is
claimed.
