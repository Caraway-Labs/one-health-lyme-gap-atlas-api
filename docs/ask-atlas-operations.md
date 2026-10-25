# Ask Atlas operating loop (API #21)

This applies to `/v1/assistant/structured` and `/v1/assistant/mixed`. The mixed
route composes the bounded Structured service and the existing governed
literature service. Both answered branches remain `insufficient_to_compare`;
live aligned/partial/discordant classification is tracked in #191. No model
can select SQL, Cypher, a tool name, or an unrestricted retrieval path.

## Bounds and meaning of signals

| Boundary | Enforced behavior |
| --- | --- |
| HTTP rate/concurrency | Structured: 30 attempts/10 minutes/IP; Mixed and governed chat share 10 attempts/10 minutes/IP because both can invoke the paid literature service. Each bucket has 3 concurrent requests/IP. Paid-capable routes also share a process-wide ceiling of 60 attempts/10 minutes and 6 concurrent requests even if IP identities change. The existing global API limiter remains. Rejections are 429 with `Retry-After`. Counters are process local; the deployed single-worker topology is required by ADR 0007. |
| Structured tools | Finite intent/question forms, at most three validated registered tool calls, bounded request fields, release and canonical evidence validation. No model call. |
| Literature | Existing one retrieval, no Snowflake spending-approval check or reservation (product-owner-authorized bypass), at most two generation attempts, no SDK retries, 28-second service deadline, each generation call at most 16 seconds, 32,000-character provider-input guard and 2,048 output-token ceiling per attempt. An incomplete provider response or exhausted input bound cannot be admitted as an answer. The character guard is not a token counter. |
| Exhaustion | Input/output exhaustion and deadline failure return no ungrounded claim. Mixed mode discards an earlier branch when the literature request bounds are exhausted; it returns `SOURCE_UNAVAILABLE` with no sources used. |

Neo4j retrieval has an absolute monotonic cap of 5 seconds or the remaining
service budget, whichever is shorter. Its request-scoped async driver covers
acquisition, transport, one explicit read transaction, eager result consumption
and cleanup. Cancellation kills the affected connection before the synchronous
worker returns; there is no detached retrieval thread or managed transaction
retry. A timeout retains the typed `evidence_unavailable` body and the `neo4j_timeout` completion; since #303 that
typed body is HTTP 200 with `Retry-After` (the DigitalOcean edge turns an origin 503 into a 504 page and drops the body).
The 28-second workflow deadline remains cooperative for other dependencies.
See [#265 cancellation evidence and rollout limits](api-265-bounded-retrieval.md).

Generation defaults to `gpt-5.6-luna` with reasoning effort `none`. Strict
Structured Outputs selects one enumerated exact sentence/adjacent-sentence
reference per atomic claim, at most two claims. Canonical citations and answer
text are assembled server-side and still pass the existing grounding validator.
The one corrective attempt receives the allowlisted validation reason. See the
[controlled live generation benchmark](ask-atlas-generation-benchmark.md) for
measured improvements, source provenance and production acceptance limits.

The request completion has a bounded `operational_outcome`: `answered`,
`abstained`, `validation_failure`, `dependency_failure`, `provider_failure`,
`budget_exhaustion`, or `internal_failure`. `service_outcome` retains the
closed literature cause when available. The public answer outcome remains the
existing contract. HTTP request validation and rate denials are classified at
middleware even when the service did not run. Missing `service_outcome` means
there was no observed literature service cause; do not infer one.
`structured_cause` is a separate closed category for a governed tool timeout,
malformed result, dependency failure, or unexpected tool exception. It never
contains an exception message or evidence payload. The public Structured
response keeps its safe `SOURCE_UNAVAILABLE` envelope; the internal cause
distinguishes validation, dependency and internal failures for review.
For a mixed request, the public `ANSWERED` outcome can retain a grounded
Structured fallback when Literature fails. The operational class follows the
recorded Literature failure, including `internal_failure` for an unexpected
service exception, so the partial success does not hide the failed stage.
An intentionally disabled Literature route has no service cause and is
classified as dependency unavailable rather than an internal exception.

`duration_ms` is the HTTP middleware boundary; `service_duration_ms` is the
literature service including connection teardown. Stage latencies are nested
inside service time; generated JSON parsing is nested inside generation. Never
sum parent and child spans, count retries as requests, or treat sampled traces
as population rates. Provider logs record input/output token counts only if the
provider reports them. Structured answers have no model tokens. No reliable
per-request USD cost is available from these events: historical reservations
were ceilings, not spend, and the current route makes no reservation. Missing
provider usage is **unmeasured**, not zero. Reconcile actual
usage with authorized provider billing and current pricing before any cost
claim. The #20 `Measurements` fields likewise remain null for unobserved
latency, tokens, cost and Phoenix export.
On 2026-10-18 the product owner explicitly authorized disabling Snowflake
spending approval and acknowledged that available budget is no longer checked.
The service's `budget_reservation` block is commented out: chat no longer calls
`SP_RESERVE_KG_LLM_BUDGET`, inserts a reservation, or emits a budget-stage timing.
The existing procedure adapter remains available for a reviewed restoration.
Rate/concurrency limits and provider input/output/time bounds remain active;
these are not dollar-spend caps. Grounding, provenance, conversation authorization
and persistence remain unchanged. A new chat may still open its lazy Snowflake
connection later for provenance or persistence; removing pre-generation connection
setup does not guarantee the entire setup cost disappears from a successful turn.
The separate public `capacity_limited` schema vocabulary remains compatible.

## Diagnose one reported failure

1. Record UTC time window, normalized route, `X-Request-ID`, deployed Git SHA,
   model ID/configuration version, assistant policy version and, where present,
   structured release and literature retrieval/corpus/index versions. Keep raw
   request IDs in restricted operator evidence; publish only a redacted summary.
   Read the model ID from the authorized deployed configuration or a validated
   answered response; the shared telemetry contract intentionally omits an
   arbitrary configured model string from span/completion dimensions.
2. Search existing safe `knowledge_chat_total` completion events by exact
   `request_id`. Read `outcome`, `operational_outcome`, `service_outcome`, HTTP
   status, bounded stage latencies, attempt and retrieval counts. If no event
   exists, check request validation, limiter and exporter health before
   concluding no request occurred.
3. In existing Tempo/Grafana traces, search `request.id=<ID>` in the same UTC
   window. Confirm the normalized server span and child
   `atlas.ask_atlas.mixed.*`, `atlas.ask_atlas.structured` or
   `knowledge_chat.*` stages share the trace ID. A missing trace may be
   sampling/export delay or outage. Use safe completion logs as the fallback;
   an empty trace search is not proof of zero requests.
4. Identify the first failing stage and the final classified outcome. For a
   latency anomaly compare same route/outcome/version windows and inspect
   service and stage boundaries without adding nested time. For a cost anomaly
   count actual generation attempts and reconcile provider usage; do not use
   reservation count as dollars spent.
5. Escalate immediately for privacy leakage, ungrounded claims, provenance
   mismatch, uncontrolled provider usage, or an undetected critical regression.
   A provider/export outage is diagnosed separately from an application answer
   failure; optional telemetry failure must not change an answer.

Safe local diagnostic commands, exercised in the #21 fixture tests:

```powershell
uv run pytest -q -o addopts='' tests/test_ask_atlas_operations.py
uv run pytest -q -o addopts='' tests/test_ask_atlas_evals.py::test_pm_selected_observation_failure_review_dry_run
uv run pytest -q -o addopts='' tests/test_ask_atlas_mixed_composition.py
```

## Hold, promote, roll back

Record the exact candidate SHA, policy/config/model identifiers, fixture
dataset version, CI result, deployment ID and deployed SHA. Hold promotion if
the complete #20 comparison gate or the manually selected v2 regression fails;
if privacy, grounding, provenance, input/output/deadline bounds or critical request paths fail;
or if required CI/OpenAPI/Docker checks fail. An unavailable live provider or
export check is **LIMITED/BLOCKED**, never PASS. Review its operational impact
before a PM-approved promotion. Keep #191 open for deferred live comparison.

For an already deployed regression, first disable the affected chat capability
with its existing feature flag if needed for safety. Use the existing quality
gated deployment workflow to promote a previously verified SHA, following
`docs/ci-and-production-deploy.md`; verify exact deployment SHA, health,
representative safe requests and correlation after rollback. Never roll back a
Snowflake evidence release by changing code alone. Keep incident evidence and
recheck model/config drift separately from code drift.

## Manual failure-to-regression promotion

Only a product-owner-selected incident enters a durable eval dataset. Preserve
the restricted incident record and request/trace diagnosis, then construct a
deidentified controlled fixture with explicit expected outcome, tool call,
release and evidence identifiers. Review provenance and privacy before commit.
Create a new version; never silently change v1 expectations or ingest every
complaint. Run both the known-good and suspect candidate through the same new
dataset; require the good candidate to pass and the suspect failure to be
detected. If the real failure cannot be reproduced safely, record it as an
unresolved gap rather than fabricating a passing case.

The #21 dry run used a controlled `get_observations` outage. The fixture
diagnosis observed that exact tool invocation followed by `SOURCE_UNAVAILABLE`
and no admitted source. The selected case was added as
`pm_selected_observation_outage` in `ask_atlas_eval_v2.json`. The existing
`bounded-v1` candidate passes it; `observation-outage-v1` fails the `outcome`
gate, so comparison blocks promotion. This is fixture evidence, not a claim
that a production incident, paid model, or Phoenix exporter was exercised.
The same fail-closed budget decision is exercised through the real Both service
with input, output and deadline exhaustion, with and without a caller-supplied
diagnostics dictionary. Non-budget dependency failure retains a valid
Structured-only fallback and reports the literature failure separately.

Keep prompts, evidence passages, answers, credentials, bearer capabilities,
network identifiers, unrestricted exception text and provider payloads out of
logs/traces and published eval results. Use the existing #156 telemetry and
#157 dashboards; this runbook provisions neither.
