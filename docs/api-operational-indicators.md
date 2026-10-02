# API operational indicator contract

Owner: Atlas API operations. Status: advisory, issue #162 under epic #156.
Consumers: API instrumentation stories #159–#161 and Grafana story #157.
This contract defines interpretation; it creates no availability promise, alert
threshold, error budget, new backend, or change to the public REST contract.

## Population and counting

Use one completed **server request span** per request for service
`one-health-lyme-gap-atlas-api`. Never count ASGI send/receive, service-stage,
dependency, or retry child spans as additional requests. Use normalized route
templates and bounded methods/outcomes. A request that retries a provider still
counts once; each provider attempt counts in that dependency's population.

The product population P is requests to registered `/v1` routes using their
supported methods. Include Atlas reads/CSV, canonical indicator/measure/
observation/source/methodology/geography reads, county/state PDF reports,
`POST /v1/knowledge-graph/chat`, account/profile, privacy-request, and feedback routes.
Report each route/method and these feature cohorts separately before combining.
Canonical geography's currently unimplemented delivery remains included as
unavailable; its documented 503 is not successful delivery.

Exclude `/health/live`, `/health/ready`, `/docs`, `/redoc`, `/openapi.json`,
documentation assets, OPTIONS preflights, unsupported methods, and unmatched
paths from P. Show health probes and unmatched/rejected traffic separately.
Include rate-limited or otherwise rejected requests before endpoint execution
when the telemetry contract resolves a registered route template. If it cannot,
show the bounded unmatched/rejection count separately; do not guess a route from
raw paths. Report this coverage gap when comparing rate-limit panels with P.
Exclude client/proxy failures that never reach the API from these server-side
indicators: they require separate existing edge evidence.

For a fixed UTC window and cohort, let:

- P = observed included completed requests, including bounded rejections.
- X = P with an unexpected application, dependency, timeout/deadline, or
  generated-output/grounding validation failure (once per request).
- U = P whose infrastructure outcome cannot be established from available
  bounded telemetry. Missing fields are unknown, not success.
- E = P − U, the evaluable requests. Preserve U and classification coverage E/P.
- D = successful delivery (2xx or valid conditional 304), including intentional
  no-evidence and safety outcomes; excludes capacity rejection.
- C = intentional capacity/protection rejection. A downstream quota or provider
  429 is a dependency failure unless explicitly classified as local protection.
- S = P with a known terminal HTTP response status; display S/P status coverage.

All ratios below use the same cohort/window/source. Zero denominator means
**no data**, never 0% errors, 100% availability, or zero latency. Missing status
or duration also gets an explicit coverage count. Do not join sampled traces
to unrelated complete logs to construct a mixed denominator.

## Indicator definitions

| Indicator | Numerator / denominator or statistic | Interpretation |
| --- | --- | --- |
| Observed volume/rate | count(P) / elapsed seconds | Retained server requests; not all requests when sampled. |
| Operational availability | (E − X) / E | Request handled without unexpected failure; expected input/auth/protection outcomes can be available without delivering a result. Display E/P, C/P, and delivery alongside this. |
| Delivery success | D / S | Valid response delivery; expected 4xx/protection are not delivered results. Display S/P status coverage. |
| Unexpected failure | X / E | Complement of operational availability; independent of transport status. |
| Transport error | included completed HTTP 5xx / S | Counts capacity 503 too; label HTTP 5xx, never label this infrastructure failure. Display S/P. |
| Client rejection | bounded expected validation/auth/not-found/payload rejections / S | 4xx explain demand and product use; not automatically server failures. Unsupported methods are separate from P. |
| Protection/capacity | C / E | Local HTTP 429, bounded concurrency/rate-limit rejection, Ask Atlas budget `capacity_limited`; split reason and route. No implicit saturation, queue-depth, CPU, or headroom claim. |
| Request latency | p50, p95, qualified p99 of completed server span duration | Same route/method/outcome cohort, observed count N and units alongside. Includes application/dependency waiting; not browser end-to-end time. |
| Dependency success/failure | successful/failed bounded terminal operations / all completed classified operations for that dependency and operation | Include attempts, show unknown count and N. Separate readiness probes from user-serving I/O, connection from query, and optional enrichment from mandatory work. |
| Dependency latency | p50/p95 of completed operation spans | Child timing identifies I/O; cannot be summed across overlapping or nested stages. |
| Ask Atlas answer yield | `answered` / all included completed chat requests with known bounded product outcome | Distinct from availability and evidence coverage. Display unknown product outcomes and each abstention category. |
| Export health | confirmed successful export attempts / completed classified export attempts, only if existing exporter diagnostics expose both counts | Not derived from request spans or trace absence. Without counters, report qualitative confirmed/degraded/unknown from existing safe diagnostics; no invented percentage. |

If a response aborts without a completion status, show the bounded failed-request
event separately and include it in X only where the request contract supplies
the included route and unexpected failure outcome. Report completeness. An
incomplete trace is not evidence of a successful response.

## Ask Atlas outcomes and causes

Use the bounded service outcome/cause, not response text or HTTP status alone.
The existing `knowledge_chat_total` completion diagnostics and Ask Atlas service
span are evidence; #159–#161 own the final attribute names and propagation onto
the request span. Until propagated, a cause missing on the request span stays U
in a request-only query. Joining a child cause to its parent is valid only with
one-to-one request correlation and deduplication, never a sum of child counts.

| Bounded outcome | Classification | Infrastructure interpretation |
| --- | --- | --- |
| `answered` | Delivered success | Grounded answer passed validation. |
| `no_evidence` | Delivered intentional abstention | No relevant corpus evidence; not dependency failure. |
| `safety_refusal` | Delivered intentional refusal | Correct policy behavior; not infrastructure failure. |
| `capacity_limited` | Expected local capacity outcome, generally HTTP 503 | Track C and delivery loss; not automatically X. |
| `invalid_conversation_capability` / input/auth rejections | Expected client outcome | Track rejection, not X. |
| `embedding_failure`, `retrieval_dependency_unavailable`, `neo4j_timeout`, `neo4j_query_failure`, `retrieval_failure` | Unexpected dependency/retrieval failure | X even when safely returned as `evidence_unavailable`. |
| `generation_timeout`, `deadline_exhausted`, `provider_rejection`, `generation_transport_error`, `generation_error` | Unexpected dependency/time failure | X; distinguish upstream rejection from local budget capacity. |
| `budget_failure`, `authorization_dependency_failure`, `persistence_failure`, `provenance_failure` | Unexpected supporting dependency/application failure | X; local budget exhaustion is different from budget-store failure. |
| `malformed_generated_json`, `corrective_retry_exhausted`, `grounding_validation_failed` | Unexpected output-quality/validation failure | X for delivery reliability, separate from infrastructure-specific dependency errors. Fail-closed safety is correct, but the intended grounded answer was unavailable. |
| `unhandled_error` | Unexpected application failure | X. |
| `evidence_unavailable` without bounded cause; disabled/unwired feature without bounded deployment intent | Unclassified unavailable | U; retain transport/delivery failure. Do not infer no-evidence or harmless protection. |

An optional provenance lookup can fail while a valid grounded answer still
returns: record a dependency failure/degraded enrichment, not global request
failure. Distinguish that case from `provenance_failure` preventing a response.
Feature-disabled behavior is a deployment/configuration condition, not proof
that the Neo4j or model provider is down.

## Latency and sample qualification

Read, PDF, account/write, and Ask Atlas latency have different work and must not
share a single unexplained percentile. Separate delivered responses, expected
rejections, and unexpected failures so a fast refusal cannot hide slow answers.
Server duration includes cache hits and misses; split only where bounded cache
telemetry is actually present. Never infer a cache hit from a short duration.
Ask Atlas service duration excludes middleware/serialization and is an
additional workflow panel, not the API request duration.

Show N for every percentile and mark small samples explicitly. p50/p95 remain
descriptive even at low N, but p95 with fewer than 20 retained requests is driven
by roughly one observation. Display p99 only when the same cohort and window has
at least **1,000 retained completed durations** (roughly ten tail observations),
and still label it sampled/advisory. This is a display qualification, not an SLO
target or a guarantee of unbiased estimation. Suppress/mark insufficient sample
otherwise; a larger window must remain inside Tempo's supported metric window.
Do not pool unrelated routes just to qualify p99. Percentile computation and N
must use the same durations; avoid averaging interval percentiles into a
whole-window percentile. Distinguish per-step from whole-window N.

## Sampling, query windows, and evidence limits

The existing root-repository collector retains error-status traces and
probabilistically samples normal traces (`OTEL_SAMPLE_PERCENT`, documented
default 10%). Tail decisions wait 5s with bounded trace memory; batching,
late/long-running spans, backpressure, and exporter loss can reduce completeness.
Verify actual deployed settings before relying on defaults. Do not interpret
the all-error policy as guaranteed complete errors when telemetry can be lost.

Observed fractions and latency distributions are biased by outcome-dependent
sampling. Error retention can inflate the retained error fraction; slow failed
requests can distort latency. Do not multiply all retained requests by ten or
divide retained errors by sampled success counts and call that a population SLI.
Even inverse-probability weighting requires verified selection probabilities
and complete trace handling; it is not currently an enforceable error budget.
Existing complete, safe request-completion logs can provide a separate
population analysis if completeness is established, without adding a log backend.

Tempo TraceQL metric queries reject ranges over 24h in the current pilot.
Use the existing API 6h default; use at most 23h for extended analysis, with
step-alignment slack (pipeline panels use a 30-minute step). Never assume seven
days of trace retention (`TEMPO_RETENTION`, default 168h) allows seven-day metric
queries. Inspect older retained traces by trace ID/Trace Explorer. Multiple
supported metric windows may be inspected separately; do not average their
percentiles into a multi-day percentile. A rejected/timed-out query is query
failure, not no traffic. Verify both query response and time range.

No recent traces can mean low natural demand, sampling, disabled export, loss,
or wrong filters/window. Show unknown until existing request logs and exporter
diagnostics distinguish these cases. Never manufacture paid inference or repeat
user questions solely to fill panels.

## Health endpoints and dependency scope

`GET /health/live` returns 200 `{ "status": "ok" }` when the process can serve
the endpoint. It performs no repository, Neo4j, provider, account, renderer, or
exporter I/O. It is excluded from all product indicators. Baseline FastAPI
instrumentation excludes it from tracing; #159 intentionally traces it as a
server request. Presence of probe spans must not change the product denominator.
It cannot prove public data, inference, export, or edge reachability is healthy.

`GET /health/ready` returns 200 `{ "status": "ready" }` only when the
Snowflake repository readiness probe succeeds, enabled chat has service wiring,
and process-local feedback idempotency has a safe worker topology. False/raised
repository readiness, unwired enabled chat, or unsafe topology returns 503.
The readiness `SELECT 1` establishes critical connection readiness, **not**
current-release view correctness, snapshot freshness, grants to every feature,
or the ability to answer an evidence question. Cached reads do not bypass it.
It may be traced, but is a separate probe population, not user traffic.

Live Neo4j/OpenAI/Supabase checks and optional provenance/diagnostics are not
global deploy gates. Neo4j is checked at chat time and fails closed. Feature
dependency failures belong on the feature/dependency panels. Enabled chat
wiring is a configuration gate; it does not probe the provider. Missing optional
account configuration can disable account routes while public readiness passes.
An exporter failure must never change liveness/readiness or user responses.
See `tests/test_health_semantics.py`, existing health/chat tests in `test_api.py`,
and the unsafe-topology test in `test_feedback.py`.

## Dashboard handoff and future enforceability

Story #157 must implement the population, outcome distinctions, N/coverage,
sampling labels, query limits, and no-data semantics above. It must select the
actual #159 server-span discriminator and bounded request attributes, the #160
dependency-operation contract, and #158 Ask Atlas dimensions when integrated.
Do not use a service-name-only selector (it counts child spans). Attribute
absence is a contract gap, not grounds to invent classifications. Keep request
IDs/trace IDs for drill-down only; never group by them, raw URL, user or query.

The proposed #159 contract (PR #163) selects SERVER spans and uses `http.route`,
`http.method`, `http.status_code`, `atlas.request.status_class`,
`atlas.request.outcome`, and `atlas.request.failure_class`. Its HTTP
`success/client_error/server_error/cancelled` outcomes are transport categories:
`server_error` is not by itself an unexpected infrastructure classification.
PR #158 supplies `atlas.ask_atlas.*` on `knowledge_chat.service` children.
Until final integration, use these as proposed mappings, verify their exact
deployed schema, and join/deduplicate service causes only as described above.
The operational contract does not require a second root span or duplicating
dependency instrumentation.

These signals are **advisory now**. Future enforceable targets require an
approved owner, population and exclusions, unbiased/complete measurement source,
window, threshold, minimum coverage/volume, deployment/version treatment,
error-budget policy, and action/alert ownership supported by natural production
baseline evidence. No threshold in this document commits Atlas to a percentage
availability or latency promise. Use the [natural-traffic validation runbook](
production-observability-validation.md) to record what was actually observed.

References: [#156](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/156),
[#162](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/162),
[public API support](public-api-support.md), [Ask Atlas latency accounting](
ask-atlas-latency-investigation.md), and root repository
`infra/observability/{collector.yaml,tempo.yaml,README.md}`. The accepted
operations/privacy boundaries remain governed by the workspace baseline and
ADRs 0002/0003 and knowledge-graph ADRs 0007–0009.
