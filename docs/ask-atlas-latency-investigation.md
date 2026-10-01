# Ask Atlas latency investigation (API #123)

Parent epic [#124](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/124).
This is diagnosis and a measurement plan, not an optimization or release.
Read-only source audit: `bbed4044ca860416c3aba7f5d0f36ee3145c9a0c`.
The chat service and middleware are unchanged from the request-reuse release.

## Business diagnosis

Snowflake-facing work and answer generation explain most of the observed wait.
Neo4j retrieval and local grounding validation are much smaller in these samples.
One matched original question returned in **21.98s after reuse versus 31.27s
before**, but embedding, generation and database stages all changed. The entire
9.29s difference cannot be credited to connection reuse. The original follow-up
took 20.07s. Three successes and two distinct failure classes do not establish p95,
capacity, a cold-start effect or sustained speedup.

The reuse story [#125](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/125)
and I/O audit [#126](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/126)
are complete. They are not recreated here. The best next latency measurement is
a private server-query-history join for the already recorded post-reuse request,
followed by provider timing/usage reconciliation for existing authorized QA.

## Measured serial waterfall

All times below are milliseconds. A is the original question and B its follow-up
on `b547565`; C is the same original question on `b959155`. All three answered
with `gpt-5.6-luna`, `single_study`, and grounding passed on generation one. Each
retrieved seven passages from two papers and cited PMID 42381666. A/C have the
same cited passage set and cited corpus provenance metadata; the complete
retrieved payload was not retained for a byte-for-byte comparison.

| Measured boundary or stage | A before | B follow-up | C after reuse |
| --- | ---: | ---: | ---: |
| Client end-to-end | 31,270 | 20,070 | 21,980 |
| API middleware boundary | 30,937 | 19,818 | 21,627 |
| Service, including request teardown | 30,919 | 19,797 | 21,605 |
| Safety classification | 0 | 1 | 0 |
| Neo4j readiness | 35 | 10 | 48 |
| Embedding | 5,862 | 582 | 3,118 |
| Neo4j retrieval/ranking | 285 | 170 | 106 |
| Budget reservation | 3,893 | 3,451 | 4,853 |
| First generation, including JSON parsing | 9,754 | 6,214 | 7,600 |
| Grounding/citation validation | 1 | 0 | 2 |
| Provenance enrichment | 3,947 | 3,336 | 1,215 |
| Conversation persistence | 7,116 | 6,025 | 4,537 |
| Service minus named-stage sum | 26 | 8 | 126 |
| API minus service | 18 | 21 | 22 |
| Client minus API | 333 | 252 | 353 |

The rows are nested boundaries, not an additive list. Add only named service
stages plus the service residual; then add API-minus-service and client-minus-API.
Rounding explains small differences within nested operations. JSON parsing is
inside generation; the separate retrieval diagnostic duplicates the retrieval
stage. Do not add either again. There is no observed stage overlap: embedding
precedes retrieval, budget precedes generation, and validated citations precede
provenance and ordered user/assistant persistence.

Snowflake-facing named stages account for **48.37% / 64.72% / 49.09%** of
service time in A/B/C. Generation accounts for **31.55% / 31.39% / 35.18%**.
C's final connection close is outside those named database stages but inside the
service residual. Neo4j retrieval is 0.92% / 0.86% / 0.49% of service.

The API-minus-service boundary combines routing/validation/worker scheduling,
serialization and middleware overhead; none is individually timed. The
client-minus-API boundary combines client transport, edge/ingress and response
delivery outside the ASGI timer. It is not a measured edge, DNS, queue or TLS
span. The middleware timer ends at response construction, not final byte delivery.
Capability authorization was not separately exercised/timed by these examples;
the follow-up used supplied history. It remains a conditional serial Snowflake
operation for a capability-bearing request.

## Snowflake server attribution and connector evidence

The private administrator CSV contains 19 rows per original request, below the
100-row extraction cap. All 38 rows report successful execution and zero queued
provisioning, repair, overload and transaction-blocked time. Those fields weaken
warehouse queuing as the explanation for these windows, not for all traffic.

| Matched top-level CALL wall time | A | B |
| --- | ---: | ---: |
| Budget | 1,200 | 953 |
| Provenance | 1,279 | 726 |
| Two persistence CALLs | 4,500 | 3,321 |
| CALL interval union/total | 6,979 | 5,000 |
| API database stages minus CALL union | 7,977 | 7,812 |

Matching uses category, timestamp/order and sessions; the supplied export did
not include explicit parent/root relationships. Contained same-session SQL is
inferred child work and is not added to parent CALL time. Compilation/execution
components are also contained in CALL elapsed time. Three sessions carry the
budget/provenance/persistence CALL groups in each original request; two extra
SELECT sessions are visible. Standalone SELECTs do not establish a fourth chat
adapter stage and are not double-counted or assigned without evidence.

Six reconstructed stage starts are about **2.36-2.56s before the first CALL**;
post-CALL remainders are about 0.13-0.21s. Reconstruction subtracts rounded API
durations from stage completion timestamps. These are strong setup/transport
clues, not measurements of authentication, TLS, session initialization or
connection construction. The 7.8-8.0s outside-CALL remainder can contain all of
those, fetch/teardown, standalone SQL, bookkeeping and boundary effects.

C directly records one setup **2,383ms**, four executes **2,463 / 1,213 /
2,598 / 1,928ms**, fetches rounded to zero and one close **113ms**. Setup is
inside budget reservation; execute/fetch are inside their stages; close explains
113ms of the 126ms service residual. Remaining rounded service residual is 13ms.
No transaction spans appeared; that is consistent with the effective AUTOCOMMIT
branch, not a separately observed session-parameter value. Execute includes
connector/transport time, so its 8,202ms total is not server SQL execution time.
Four bounded query IDs are retained privately for the missing fresh server join.

Snowflake named stages fell **4,351ms** from A to C; embedding plus generation
fell **4,898ms**. Budget reservation increased 960ms. This does not isolate
reuse's causal contribution. In a separate read-only DEV desktop/CLI control,
three fresh constructions totaled 2,175.405ms versus one reused construction
723.695ms in the reversed-order pair. Different driver/authentication/network
context and an initial 13,880.389ms outlier prevent extrapolation to production.

## Model, cold/warm behavior, failure and deadlines

Source uses low reasoning effort, at most one corrective generation, SDK retries
disabled, and no explicit output-token cap. Visible answers contain only
285 / 365 / 307 characters and 2 / 3 / 2 claims. These character counts exclude
provider JSON/support quotes and hidden reasoning; they are not token counts or
evidence of provider compute time. The generation spans include transport and
JSON parsing. Actual input/output/reasoning tokens, cache usage, service tier,
first-token timing and billed cost are absent from retained API/provider records.
Do not choose a model/reasoning/output setting based solely on these spans.

Embedding varies from 582ms to 5,862ms. The shared OpenAI client already reuses
HTTP transport. No aligned process age, HTTP connection/handshake or provider
queue evidence proves A cold and B warm. Neo4j readiness/retrieval is measured,
but internal query-plan/index/network costs are not separated. No index change
or database/worker capacity increase is justified by these single-request samples.

An earlier failed canary had a client edge 504 at about 20,040ms and a backend
typed 503 at about 19,700ms after two generations emitted an uncited claim. Its
retired-deployment detailed spans are unavailable. Do not allocate that failed
request's time between attempts or infer a configured edge timeout from the
observed boundary alone. [Prompt repair #122](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/pull/122)
preceded A/B; all three successes validated on the first attempt.

A later, separately owned QA request is a different failure class, D:

| D boundary/stage | Milliseconds |
| --- | ---: |
| Client edge 504 | 3,148 |
| API boundary, backend typed 503 | 3,012 |
| Service | 3,005 |
| Safety classification | 1 |
| Neo4j readiness failure | 3,003 |
| Service residual / API minus service / client minus API | 1 / 7 / 136 |

D's outcome is `retrieval_dependency_unavailable`, generation attempts zero,
validation not run. It failed before embedding, retrieval and generation; there
is no provider response record. It is not part of a successful-latency average or
evidence that LLM execution is slow. Operator-side graph/index health does not
prove the API-to-Neo4j path works. The QA owner owns connectivity diagnosis and
the new bug; this report attributes the observed failure stage without duplicating
that investigation. The backend/edge status difference remains a boundary fact,
not proof of an edge timeout setting or of the connectivity cause.

The nominal 28s service deadline is checked before generation, and generation
is limited by the remaining budget less a 5s post-generation reserve. It is not
a hard end-to-end deadline on post-generation database work. Provenance plus
persistence took **11,063 / 9,361 / 5,752ms**; C also has 113ms close and 2ms
validation. All exceed the nominal reserve, and A's service exceeded 28s.
Driver timeouts and asynchronous request cancellation do not guarantee immediate
interruption of synchronous worker I/O. Returning success before durable writes
would change the product contract; no such recommendation is silently assumed.

## Ranked decisions and falsifiable next experiments

Costs below identify affected work, not additive forecasts of removable savings.
No future paid experiment is authorized by this report or the separate QA budget.

| Rank | Decision / measured target | Confidence; effort / risk | Specific next experiment |
| --- | --- | --- | --- |
| 1 | Attribute remaining Snowflake work: C has 2.383s setup, 8.202s execute and 0.113s close | High stage cost; medium internal attribution. Small measurement effort / low read-only risk | Export C's four existing query IDs with parent/root/session links, elapsed/compile/execute/queue/block fields and safe categories. Join privately to connector spans; subtract matched wall intervals without adding children. Optimize only an implicated CALL or boundary. |
| 2 | Establish a coherent deadline and durable-success contract; reserve is insufficient in all three samples | High source/measurement confidence. Small offline experiment; medium later behavior-change risk | Use existing fake-clock/slow dependency fixtures to exhaust time during provenance, both writes and close. Define client/edge/API deadlines and typed failures with product owner. Do not change production timeouts merely to mask late work. |
| 3 | Explain 6.214-9.754s generation, and failed corrective attempts | High duration; low token/compute attribution. Small receipt reconciliation; medium later quality risk | Reconcile existing authorized QA provider receipts for model/tier, input/output/reasoning/cache tokens and timing. If still necessary, separately allocate fixed-evidence, one-variable comparisons with unchanged grounding and retry/error/cost checks. |
| 4 | Examine serial post-generation persistence/enrichment; C has 5.752s plus close | High dependency/source confidence; avoidability unknown. Medium fixture effort / medium-high durability risk | Compare equivalent paired-turn transaction/round-trip designs offline, preserving ordering/idempotency/error behavior. Prefetch/overlap only after proving candidate provenance independence and safe ownership. Ideal C provenance overlap is at most 1.215s before overhead, not a prediction. |
| 5 | Explain embedding variability; C is 3.118s | High duration; low cold/warm attribution. Small existing-log reconciliation / medium privacy risk for caching | Align existing process age, transport and provider metadata. Measure cache eligibility/hit rate using exact input/model-version keys in approved fixtures before considering caching. No warmup calls are authorized. |
| 6 | Revisit Neo4j plans, worker/edge capacity or async rewrite only if new evidence changes priority | Low evidence of current dominance. Medium measurement effort / avoid speculative changes | Use existing plans/diagnostics and passive traces; measure dispatch/ingress only if needed. #126's fake slow-worker regression proves scheduling correctness, not load capacity or hard cancellation. No load test, index or thread-count change now. |

Cross-request pooling is not a repeat of the completed request-local reuse:
it needs a demonstrated remaining setup benefit and session-state/credential/
transaction isolation, stale recovery and bounded acquisition evidence.
Caching cannot skip capability/budget admission, provenance freshness or grounding.
Overlap changes the critical path, not the amount of work; its bounds overlap
database/reuse/round-trip bounds and must never be summed.

## Evidence, reproduction and exact gaps

- E1: baseline A/B stage summaries and retained source/trace snapshots;
  [original server-history reconciliation](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/123#issuecomment-5925002772).
- E2: private 38-row administrator CSV reconstruction and sanitized analysis.
  Empty role-limited history is not proof of absent queries; no grants or
  warehouse/role changes were made to bypass the visibility limit.
- E3: [matched C canary](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/123#issuecomment-5926070030)
  and [#125 bounded closure](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/125#issuecomment-5926275454).
  Both ordered persistence executes succeeded and a capability returned;
  stored-row readback, capability-bearing follow-up and browser reload are not
  independently verified. No public GET conversation/history path exists.
- E4: [I/O audit](ask-atlas-external-io-audit.md),
  [reuse evidence](ask-atlas-snowflake-reuse.md), and existing offline regressions.

[The machine-readable summary](evidence/ask-atlas-latency-summary.json) contains
only safe aggregate timings. Private reproduction selects records by request
correlation, distinguishes named-stage from nested-operation spans, matches CSV
CALL interval unions, and recomputes duration differences and percentages using
the service denominator. Retained snapshots, extraction notes, sanitizer and
SHA256 receipts remain in the isolated local evidence workspace. Question,
history, conversation capability, request/provider/query/session IDs, internal
addresses and full logs are not included in this report or its JSON.

The initial refreshed runtime tail contained no Ask Atlas stage/provider records.
A later bounded tail privately correlates the QA owner's already executed D
request to two stage records and its total. Existing authorized QA evidence is
reused; no request was made for this investigation wave and no QA-batch budget
was used. Additional successful batch receipts were not available in the
inspected artifact locations.
Missing referenced parent-workspace guidance/ADRs were recorded; available repo
instructions, README and deployment guidance were followed.

Exact follow-on gaps are C's server-query join; provider usage/tier/compute-versus-
transport timing; process/connection age for cold/warm classification; independent
edge/dispatch/serialization spans; the retired failure's attempt spans; and
readback/follow-up/UI acceptance. None is concealed as zero or estimated p95.
They are bounded next evidence requests, not an assertion that diagnosis must
identify every internal component before the five written investigation criteria
can be satisfied. No implementation ticket or speculative optimization is added.

References: [FastAPI synchronous worker dispatch](https://fastapi.tiangolo.com/async/),
[Snowflake stateful session reuse](https://docs.snowflake.com/en/developer-guide/driver-connections),
[API #120 observability](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/120),
[KG #13 live acceptance](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-knowledge-graph/issues/13).
