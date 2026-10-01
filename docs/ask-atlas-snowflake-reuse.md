# Request-scoped Snowflake reuse (API #125)

Parent epic #124; baseline evidence #123; I/O audit #126.

The production path previously constructed separate connections for budget,
provenance and conversation persistence, plus capability authorization when
present. The new path owns one lazy connection within the synchronous chat
worker. Each adapter still owns a separate cursor, runs serially, and keeps
its commit/rollback boundary. Both conversation turn procedures remain ordered
within one adapter operation. The connection closes before service completion,
including exception and capacity/refusal outcomes; no cross-request pooling
or global cached credential/session state is introduced.

The exact settings object must match to reuse a connection. Standalone adapters
and injected stores retain their original connection context. Request context
is reset in finally, and an owner-thread check rejects propagated context used
by another worker. A closed connection fails rather than reconnecting and
replaying an uncertain budget reservation or write. The maximum is one live
reused connection per active chat request; existing request/worker limits apply.

The shared connection factory does not set AUTOCOMMIT in session parameters.
Its connector context therefore commits/rolls back at adapter exit. Reuse
preserves those operation boundaries explicitly while moving only physical
close to request teardown. No SQL/schema/transaction policy change or parallel
Snowflake statement execution is intended. Partial-turn failure stays fail-closed
and is not retried automatically.

Nested diagnostics record connection setup, transaction commit/rollback,
operation execute/fetch and connection close, with bounded query IDs where
available. They contain no SQL, parameters, prompts, questions/history or secrets.
They are excluded from additive stage totals: their durations are contained in
adapter stages (except final close, which contributes to service residual).
Query IDs allow private server-history correlation. Connect timing measures the
factory call, not exclusively authentication or network negotiation.

Existing generation admission remains after embedding/retrieval and before
answer generation. The pre-existing paid-embedding-before-reservation gap is
documented, not silently moved: moving admission would change no-evidence budget
behavior. A regression test makes the retained ordering explicit. No paid calls
were used to develop or validate this change.

## Evidence and limits

Baseline A/B: 14,956/12,812ms Snowflake-facing API spans; 6,979/5,000ms matched
server CALL intervals; 7,977/7,812ms outside-CALL remainder. Six reconstructed
stage starts had 2.36-2.56s before the first CALL. That remainder is not proven
setup cost. Connection counts in deterministic adapter tests change from three
to one for an answered request, or four to one with capability authorization.
No provider/model/input/grounding/retention settings changed.

The bounded DEV read-only control uses the existing DEV read identity with
minimal SELECTs and separately times fresh connections versus reuse. It is
a desktop/CLI connector control, not the production API or procedure workload;
do not extrapolate its gains directly to end-to-end answers. The actual deployed
API needs a separately allocated matched live canary and private query-history
correlation before any production latency improvement is claimed.

Two bounded desktop DEV control pairs each ran three minimal SELECTs. The
second pair used reversed order: reused connect 723.695ms versus fresh connect
716.618 + 678.056 + 780.731 = 2,175.405ms. Connection-construction difference
was 1,451.710ms; query execution totals were 573.660ms reused versus 301.595ms
fresh, so server/transport variability remains. Close totals were 159.171ms
reused versus 819.078ms fresh. These component times exclude per-connection
role-verification SELECTs and are not complete end-to-end timings. The first
fresh construction was an initialization outlier at 13,880.389ms; it is retained
in private evidence and is not used as a typical savings estimate. Eight total
DEV connections, twelve control SELECTs plus role checks, no writes/warehouse
state changes, and no provider calls were used. Two pairs do not establish p95.

## Rollout / rollback

Run focused lifecycle/isolation/failure/privacy tests, full regression suite,
lint/types, unchanged OpenAPI export and Docker/CI. Parent independently reviews
the PR and coordinates the API production slot; shared DATA deployment is untouched.
After deployment, inspect sanitized connect counts and stage/query correlation.
If authorization, budget, provenance, durability or cleanup regresses, revert
this focused commit through the existing API promotion process. No environment
toggle, grant, credential, warehouse or schema change is required.

Reference: [Snowflake session reuse constraints](https://docs.snowflake.com/en/developer-guide/driver-connections).
