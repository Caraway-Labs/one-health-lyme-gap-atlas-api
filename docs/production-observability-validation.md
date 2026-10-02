# Natural-traffic production observability validation

Owner: Atlas API operations; #162 / #156. Companion:
[indicator contract](api-operational-indicators.md). This validates the existing
deployment with existing natural traffic; it does not generate paid inference,
dispatch jobs, alter sampling, create credentials/grants, or deploy a new stack.

## Before inspection

1. Record deployed API commit/version, instrumentation PR versions (#158–#161),
   dashboard/root commit (#157), UTC start/end, and inspection time. Distinguish
   source-controlled defaults from verified deployed collector/Tempo settings.
2. Use existing authorized Grafana access and existing App Platform safe runtime
   logs/exporter diagnostics. If access is unavailable, record the exact missing
   capability. Do not retrieve or paste tokens, headers, credentials, or raw
   unrestricted logs into the evidence record.
3. Choose a 6h recent window and the same cohort/window for numerator,
   denominator, durations, and counts. Extend to 23h if natural volume is sparse.
   Verify queries succeed; record step and any panel override. A 400/timeout is
   a query problem, not zero requests. For older retained incidents use trace
   lookup rather than an unsupported metric range.

## Inspect existing traffic

1. Inspect server request volume by normalized route/method and status. Confirm
   health/docs/OPTIONS/unmatched requests and child spans are excluded from the
   product population. Compare with existing safe completion logs in the same
   time range to establish traffic occurred; do not assume sampled counts match
   full counts. Record observed P, E, U, status/duration coverage, and source.
2. Open one naturally occurring public-read trace, and a PDF/write/Ask Atlas
   trace if present. Confirm one server span, bounded route/status/outcome,
   request ID correlation with its safe completion event, and dependency parent/
   child relationships. Do not copy a prompt, answer, evidence, SQL/Cypher,
   query values, user identifier, IP, authorization, or exception message.
3. Inspect delivered/no-evidence/safety/capacity outcomes separately from
   dependency, timeout, validation, and unhandled failures. A local capacity 503
   belongs in transport errors and capacity, not automatically infrastructure
   errors. `evidence_unavailable` needs a bounded cause. If a category did not
   naturally occur, record **not observed**; its mock regression test is separate
   evidence and cannot establish that production emits it correctly.
4. Inspect p50/p95 with retained duration N per route/outcome cohort, and p99 only
   when N >= 1,000 in that same displayed interval/window. Record insufficient
   samples explicitly. Verify unit and whether N is per-step or whole-window.
   Do not sum nested spans, count generation retries as requests, average
   percentiles, or infer population SLIs from outcome-biased samples.
5. Inspect bounded Snowflake, Neo4j/provider, and any observed account dependency
   operations. Record failures/timeouts and optional enrichment degradation
   separately. An empty Neo4j panel without chat traffic is **no observations**,
   not proof of dependency availability. Child retries can fail while a request
   ultimately succeeds; preserve both interpretations.
6. Inspect existing platform health-check results: liveness is process-only;
   readiness is Snowflake connectivity, enabled-chat wiring, safe topology.
   Correlate readiness failure with dependency diagnostics where available.
   Do not induce a production outage to validate the negative paths.

## Export and query health

Use the existing safe exporter initialization warnings/failure diagnostics and
collector/Tempo container status/logs available to the operator. Record bounded
failure class, time window, and whether recent naturally occurring traces arrived
after batching/tail-decision delay. Do not paste unrestricted exporter errors
that may contain endpoint/header details. A recent trace confirms that its path
worked at that time, not that all exports succeeded. Without existing attempt
counters no export success percentage can be computed.

| Evidence | Interpretation/action |
| --- | --- |
| Recent safe request completions and recent correlated traces | Export confirmed for those observed requests; other successes can be sampled out. |
| No request completions and no traces | Low/no demand or insufficient evidence; check access/window. Health remains unknown from traces alone. |
| Requests present, export warning/failure present, no arrivals | Export degraded; API may still be healthy. Use existing incident path, not readiness gating. |
| Requests present, no trace match, no exporter diagnostic | Unknown: sampling, filters, late spans, or loss; inspect supported wider window and existing collector status. |
| Metric query rejected/timed out | Query/window failure; repair range/filter and record it separately from API/export health. |
| Trace found but bounded field absent | Deployed instrumentation contract gap; report version and missing field, do not infer a value. |

Inspect already retained traces and existing diagnostics only. This runbook does
not authorize changing retention, sampling, collector topology, access grants,
or logging sensitive content. Escalate any such separate change to its owner.

## Evidence record and completion

Create a privacy-safe record using the template below. Keep detailed trace/
request identifiers in the existing restricted operator evidence location;
public PR evidence can report correlation confirmed without publishing them.
Do not claim the acceptance checks observed a production failure/capacity branch
when it was only exercised with a fake dependency.

```text
API/dashboard commit and deployed version:
Inspection time and exact UTC window/step:
Access/source: Grafana / safe runtime logs / existing exporter diagnostics:
Actual deployed sampling/retention known? (value or not verified):
Query status and supported window:
Included route/method/outcome cohort:
Retained P/E/U; status/duration coverage; D/C/X:
p50/p95; duration N; p99 qualified or insufficient sample:
Public request correlation/server-span count: observed / not observed / blocked:
Ask Atlas answered/no-evidence/safety/capacity/failure: each observed / not observed:
Dependency operations and nested timing: observed / not observed / blocked:
Liveness/readiness evidence and interpretation:
Exporter evidence: confirmed for observed trace / degraded / unknown:
Privacy check (bounded fields only):
Mock-test evidence (separate, exact test/CI commit):
Remaining low-volume categories, access blockers, owner and next natural window:
```

Completion means queries work within supported windows, observed traffic follows
the contract, and all missing/low-volume outcomes are explicitly recorded with a
follow-up owner. It does not mean every branch occurred or an enforceable SLO
was proven. Parent independently reviews the fresh-main integration before
merge; use the existing quality/deployment path described in
[CI and production deployment](ci-and-production-deploy.md). After deployment,
repeat read-only inspection on natural traffic and record deployment-specific
evidence rather than reusing pre-deploy mock or trace evidence.
