# API #243 climate request latency

## Retained production evidence

API #84 is closed; no Data ingestion, publication, migration or scientific
reacceptance is part of this change. Release:
`governed-2026-10-15-january-climate-prod`.

Eight accepted public requests at API main
`b9ce2560d693d83df3001374a2d2ef15e1a1ea0e`, deployment
`19fa8dbc-1a24-48e0-bd7f-24f57c5b87e9`, returned 200 in
8.313–10.000 seconds (median 8.5545). Retained middleware completions for
that acceptance window were 8.099–9.688 seconds (median 8.2805).
The retained client results did not preserve request IDs, so chronological
agreement does not establish exact per-request correlation. Do not subtract
these samples to estimate network time.

Source: [API #84 acceptance](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/84#issuecomment-6228345995),
[API #243](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/243).
Retained middleware request IDs/durations (milliseconds):

| Request ID | Duration |
| --- | ---: |
| 334ff622-d9a4-4dcd-a98c-cbb6e78baf53 | 8946 |
| 512361ba-8955-4b6d-978c-40406d3634f9 | 8201 |
| f80df5a1-0634-4e41-8456-260b93eb9fb7 | 8099 |
| 71a52189-f028-4e82-a18c-bf1b1521c609 | 8135 |
| 935eaea3-b4aa-499e-b5d9-71351f268df9 | 8377 |
| bbefc013-ebb0-49c3-8523-75545df9d893 | 8205 |
| 9e821c3e-956d-4b60-8cf6-5a2c92b19218 | 9688 |
| d2aa2aba-3f2a-47fb-ab1a-edaa9efb7845 | 8397 |

These processing intervals contain most of the observed latency. They do not
attribute it to authentication, SQL or Python. Different-context Data reads
(2.016 seconds full projection / 0.547 seconds eight rows) cannot establish
API overhead. Neither dataset establishes p95, warm behavior or a guarantee.

## Actual canonical path

`GET /v1/observations`, with `measure_id`, `geography_type=county`,
`geography_id`, `start_date` and `end_date`, is the existing route in
`public_routes.observations`. Anonymous public reads have no token
verification. App Platform edge/TLS precede ASGI; their durations are unknown.
Existing CORS, request context, public bounds/rate/concurrency protection,
GZip and route/Pydantic validation surround the synchronous FastAPI handler.
The handler runs in the threadpool, not as blocking I/O in an async handler.
Dependencies retrieve the shared `ObservationService` from app state.

`ObservationService.search` calls these sequentially:

1. `SnowflakeObservationRepository.current_release`: current published release.
2. `EnvironmentalRepository.measure_exists`: climate metadata bound to that release.
3. `EnvironmentalRepository.query`: full scientific projection, exact county,
   date and measure filters, release ID, bounded pagination.
4. Numeric decoding, `environmental_observation`, envelope construction,
   optional existing surveillance resolution, response validation/serialization.
5. ASGI sends the JSON body; edge/client complete transport.

Before this change, each of the three repository reads constructs its own
connection through `dependency_telemetry.connect` and shared Snowflake
`connect/connection_parameters`: RSA key loading, authentication, session setup
and teardown are repeated three times. There is no pool in this path. This is
an established avoidable lifecycle cost; its actual share of production latency
has not yet been measured. Three SELECTs remain required by the current gate.
No cache, query consolidation or Data serving change is justified by current
evidence. Cold warehouse and cold API process effects remain unmeasured.

## Correction and invariants

For an enabled climate request only, the service opens one exclusively owned
connection and creates a request-local repository borrowing it. All three
reads still create/close their own cursor and retain identical SQL, parameters,
release predicates, timeout arguments and approved credential construction.
The shared application repository is never mutated. The connection closes
once as the request exits, including failures. There is no cross-request reuse,
pool, metadata cache or stale release TTL. Annual/CDC and standalone metadata
paths retain their existing lifecycle.

Every request still looks up the current release. A pointer change between
statements retains the existing fail-closed/no-old-release result behavior
because both subsequent current-view reads explicitly require the captured
release ID. Pagination retains release binding. This is not transaction-wide
snapshot isolation and does not claim to strengthen it.

Offline tests compare the same three SQL statements/binds/results before and
after. Connection count is 3 → 1. A deterministic illustrative cost model
(1000ms per handshake, 100ms per query) gives 3300ms → 1300ms; those numbers
are assumptions, not measured local or production latency predictions.
Scientific fixtures prove four measures, measured zero, negative temperature,
null/out-of-coverage, no-data 200, exact identity and sanitized provenance.

A separately executed local TestClient comparison used the real HTTP/service/
adapter path with a fake connector delaying 100ms per connect and 10ms per
execute. Eight sequential PRCP requests per mode, identical scientific rows:

| Simulated local HTTP | Median ms | Maximum ms | Connections / SELECTs |
| --- | ---: | ---: | --- |
| Original independent sessions | 343.8655 | 420.367 | 24 / 24 |
| Request-owned session | 143.1635 | 157.875 | 8 / 24 |

Individual elapsed milliseconds, original:
`420.367,344.955,342.300,343.574,342.074,343.611,344.459,344.120`;
request-owned:
`157.875,141.841,144.877,143.458,144.389,142.435,141.689,142.869`.
All 16 responses were 200 with measured-zero fixture decoding and every fake
session closed. Original mode used a test-only subclass whose climate_request
context yields the original unbound repository, reproducing the original three
adapter acquisitions; candidate used the actual new service path. Artificial
delays are deliberately controlled, not estimated from Snowflake. This proves
the correction removes two handshake costs; it does not predict production
latency, cold/warm warehouse behavior or p95. No external request was made.


## Timing, correlation and remaining unknowns

Existing FastAPI OpenTelemetry instrumentation and dependency CLIENT spans
already exist; OTLP configuration keys are present in deployed configuration.
That does not prove exporter delivery, and no accessible retained trace was
found that attributes these eight requests. No new backend/provider/exporter,
sampling policy or public diagnostics endpoint is added.

One bounded timing summary is added to the existing middleware completion
for climate requests. It is recorded using monotonic clocks and survives the
sync handler's threadpool through request state. Existing `X-Request-ID` and
SERVER span `request.id` provide correlation. Trace IDs stay in trace context;
query IDs, SQL, geography, measure, values, source records and credentials are
not emitted, per the existing telemetry privacy contract.

| Measurement | Boundary / limitation |
| --- | --- |
| `duration_ms` | Existing middleware interval; not final network completion |
| `climate_before_service_ms` | Middleware start → handler service start; combined protection, validation, scheduling; not isolated auth/network |
| `climate_after_service_ms` | Service end → middleware completion; combined response validation/serialization; not final send |
| `service` | Service including connection lifecycle and mapping |
| `release_lookup`, `measure_lookup`, `observation_query` | Three logical reads including cursor/fetch work |
| `mapping` | Scientific row conversion only; not JSON transport |
| `connect` | Shared connector construction/authentication/session initialization combined |
| `execute` | All three SQL calls combined; includes connector/network; compilation vs execution unknown |
| `fetchone`, `fetchall` | Actual fetching/conversion at connector boundary |
| `cursor_teardown`, `connection_teardown` | Actual SDK context exits |
| operation counts | Closed names; confirms expected one connect / three executes |

Service, logical phases and dependency intervals overlap: **never add them all
as independent wall time**. Existing SERVER trace includes final ASGI send.
Server compilation/execution, warehouse provisioning, and network require
matched retained server/trace evidence and remain unknown until obtained.
No guessed FastAPI/network residual is reported. The startup reader probe caps
each SELECT at five seconds; runtime repository timeout arguments are unchanged.
`ApiSettings.public_query_timeout_seconds` has a source default of 15 seconds;
this change does not silently alter it or infer deployed values from probe caps.

## Controlled production acceptance boundary

No new Snowflake session, HTTP climate probe, merge or deployment is executed
under this engineering assignment. Prior DATA budgets/consumed attempts are not
API #243 authority. Human merge is required by AGENTS.md and triggers the existing
Quality/deploy path; no alternate deployment workflow is introduced.

Proposed one consolidated authorization after review/green exact head:

- One baseline workload on the current deployment, then human merge of the
  reviewed head and existing production deployment/review. No duplicate deploy.
- Baseline eight unique GETs (48277 and 02013 × PRCP/TMIN/TMAX/TAVG, January17),
  then the identical eight on the new deployment plus one repeat of that eight
  for a labeled warm-order comparison. No retries or redirects. Each HTTP request
  has a 20-second client deadline; stop on any failure or scientific/release
  mismatch. Record request IDs, deployment SHA, monotonic durations and bodies
  decoded into safe acceptance assertions, never restricted data in logs.
- Existing startup probe: maximum three SELECTs, five-second cap each. Workload
  maximum 24 HTTP requests / 72 SELECTs. Statement timeout arguments unchanged;
  no cache disable, warehouse resize, grant or infrastructure changes.
- Baseline process deadline 180s; postdeployment 360s; no polling invokes climate.
  Health/deployment metadata and retained logs/traces are read-only control-plane
  checks. Preserve environmental context enabled and all unrelated settings.
- For reservation only: COMPUTE_WH XS Standard Gen1, one credit/hour; conservative
  $20/credit planning rate (not contractual billing evidence), at most 660 seconds
  active test/startup window plus 600 seconds retained auto-suspend tail =1260 seconds,
  **$7 forecast, $10 maximum incremental allocation**. Owner must approve this
  fresh allowance; actual billing and concurrent unrelated warehouse use remain
  unknown and are not claimed as measured cost. Stop if size/rate/resource
  assumptions or scope differ. Platform hosting cost is existing capacity.
- Capture median/max separately before/after, individual durations, order and
  warehouse/process warmth limitations; no p95 claim from this sample. Do not
  attribute improvement to SQL execution by subtracting unmatched contexts.
- Failure: stop without retry; preserve evidence. Existing controlled rollback
  to the prior accepted source SHA requires explicit rollback authority included
  in deployment approval; no Data pointer rollback or database mutation.

Under-five-second HTTP is an acceptance objective, not an achieved result.
If repeated handshakes were not dominant, the new timings identify the next
specific limiting stage without inventing another serving architecture.
