# Public V1 API support and release gate

Owner: Atlas API maintainers. This runbook covers the anonymous canonical `/v1` read API. The committed `openapi.json` is the contract; `tests/fixtures/public-v1-compatibility.json` is a selective compatibility floor captured from it, not a second schema. CI also protects the separate first-party build artifact without exposing
internal product operations in the public schema.

## #58 audit at `3524863565705c194e5bdf65ffad93af2e6aee0d`

| Requirement | Starting state | #58 disposition |
| --- | --- | --- |
| Contract drift | CI regenerated OpenAPI and required an exact match; #52 tests checked public paths and selected fields | Add an additive compatibility floor for required fields, nullable values, enum states, problem responses, pagination, provenance, and release fields |
| Consumer workflows | #53–#55 endpoint tests cover metadata, bounded observations, missing and `NO_COUNTY_LINKED_RECORD`, pagination, source and methodology; #57 proved a live discovery-to-provenance round trip | Retain those tests; add a bounded production probe and release gate |
| Protection | #56 tests and production smoke cover 400/404/413/414/429, signed tokens, conditional 304, bounds, and 15-second Snowflake timeout | Retain existing controls |
| Performance | No repeatable large allowed-shape timing gate | Add fixture-backed one and 400 county shapes plus a five-request burst; measure live latency without sustained production load |
| Observability | Structured completion logs include status and duration; categorical 429 and conditional hit events; App Platform readiness, CPU/memory alerts, Snowflake query history | Add log summarizer and metric definitions; disable Uvicorn address-bearing access logs and use route templates |
| Deprecation | Contract states intended 90-day notice but lacks a procedure | Define notice, compatibility review, and retirement steps below |
| Rollback | Exact-source deployment and revert guidance already in `ci-and-production-deploy.md` | Add public smoke and support decision path below |

The current production app is one instance and one worker. Its rate limiter and pagination signing key are process-local. Do not scale instances or workers without revisiting those controls.

## Release gate

1. Review the PR against `openapi.json` and the compatibility floor. New optional fields and enum values can be additive; removing a path, narrowing an enum, removing nullable support, or changing required fields needs a reviewed version/migration decision. Do not refresh the fixture merely to make a failing check pass.
2. Run the existing `quality` job: locked sync, Ruff, mypy, pytest, OpenAPI generation equality, Docker build, Typst regression, and secret scan. `test_public_compatibility_gate.py` and `test_public_performance.py` run with pytest. A PR quality run never deploys.
3. Merge only after owner review of contract and public interpretation. A green push to `main` promotes only that exact current-main SHA. The deploy job confirms App Platform `ACTIVE` and its `source_commit_hash`; see `ci-and-production-deploy.md`.
4. The deploy job runs one bounded public smoke after promotion. It checks discovery, observations, missing-value behavior, source/methodology release consistency, and typed unknown-measure rejection. For a deeper characterization, run `uv run python scripts/probe_public_api.py` manually; it samples three sequential requests per shape, including 400 county selectors and page size 500. The repeated-parameter URL cap makes a 500-selector GET exceed 8,192 bytes, so 400 is the practical large request fixture.
5. Inspect the active SHA, readiness, smoke output, and recent request/error logs. Record the merge SHA, deployment ID, smoke result, and any open limitation on #58 or its PR.

The fixture-backed timing limits guard local API serialization and pagination overhead, not Snowflake latency. The live probe fails a release when any sampled request reaches 20 seconds. This is a provisional release ceiling below the client's 30-second timeout, not a measured SLO or a concurrency capacity claim.

Initial production sample on 2026-09-30, before this change, from active API SHA `3524863`: three sequential reads per shape. Median/max milliseconds were indicators 122/3,490, measures 146/2,978, one county 10,343/10,686, three counties 9,430/10,003, and 400 selectors 10,778/11,087 (67 returned observations). The slow observation baseline merits continued review; it does not justify calling 10-second reads fast. The service currently performs a release check, measure check, and bounded observation query through separate Snowflake connections. Changing that read path requires its own measured, source-backed optimization and regression review.

A same-day App Platform log sample contained 31 canonical public completions, zero 5xx, zero 429, four conditional detail hits, and 9,081/10,525 ms median/p95 across that sample. The sample is small and biased toward the probe; it is evidence that the metrics can be recovered, not an availability estimate.

## SLO-oriented operating signals

Use a fixed reporting window and record the sample count with every percentage. These signals support owner-set SLOs once a representative production baseline exists; this story does not claim an unmeasured availability or latency target.

| Signal | Numerator / denominator or statistic | Current evidence source |
| --- | --- | --- |
| Availability | Successful external `/health/ready` probes / attempted probes; separately count public 5xx / public requests | App Platform readiness status, external probe, structured completion logs |
| Latency | Median and p95 `duration_ms` for canonical `/v1` routes, split by route and status when investigating | `api_request_completed` JSON run logs; live probe samples |
| Errors | 5xx count / canonical public requests; track 400 and 404 separately as client outcomes | Structured completion logs |
| Rate-limit rejections | 429 count / canonical public requests; categorical `public_read_rejected` reason | Structured completion and protection logs |
| Cache effectiveness | 304 detail responses / detail GETs plus `public_read_conditional_hit` count; edge `Age` is evidence of caching, not a hit ratio | Structured logs and response headers |
| Backend pressure | Public-read query count, duration, queued time, errors/timeouts for the API service role; CPU/memory over 80% for five minutes | Snowflake query history under the approved read-only audit role; App Platform CPU/memory alerts |

For a bounded log sample, run:

```powershell
doctl apps logs fb312ce3-e762-45b0-bf34-12d3df91eee6 api --type run --tail 1000 | uv run python scripts/summarize_public_logs.py
```

This summary is only the returned log sample; it is not a durable dashboard or a monthly SLO measurement. App Platform already has active domain, deployment, CPU, and memory alerts. Investigate readiness failure or public 5xx immediately. Rising latency, 429, or Snowflake queue time calls for a time-window comparison before changing bounds or capacity. Do not log addresses, query strings, bodies, bearer tokens, prompts, or SQL results. Application route labels use templates; Uvicorn access logs are disabled. OpenTelemetry tracing remains available through the existing shared package configuration.

## Deprecation and compatibility

Maintain V1 additive compatibility by default. Before proposing removal, rename, changed meaning, or a stricter accepted value:

1. Inventory consumers, including the web app, public examples, generated clients, and known partners. Record the replacement route/field and confirm equivalent semantics and provenance. An observation measure cannot be replaced by a similar-looking but scientifically different value.
2. Obtain an owner-reviewed ADR for a breaking public change, with a migration plan, version path, notice date, earliest retirement date, and rollback. The intended minimum notice is **90 calendar days** before retirement where operationally practical. Security emergencies require an explicit owner exception and immediate notice.
3. Publish the notice in the API guide and release notes, including exact affected operations, replacement examples, and dates. Mark the affected OpenAPI operation/property `deprecated` while it remains served. Do not emit a sunset date or claim migration is complete until a date and consumer plan are approved.
4. Preserve compatibility fixtures and tests for both old and replacement behavior during the notice window. Measure use where privacy-preserving telemetry permits; absence of observed traffic alone is insufficient proof of no consumer.
5. After the notice window, confirm owner approval, consumer migration, live smoke, and rollback plan before retirement. Update `openapi.json`, the floor, docs, and tests in the reviewed change. V1 breaking semantic changes require a new version path.

The existing `Source.published_at` and `Source.atlas_acquired_at` properties are marked deprecated in OpenAPI. They remain nullable and supported; no retirement date is set.

## Incident and rollback

If the smoke fails after a promotion, check whether the failure is a transient backend/readiness issue, a response contract change, or a deployment mismatch. Confirm App Platform active deployment `source_commit_hash` against the reviewed main SHA. Preserve the failing status, route template, timestamp, and deployment ID without request data.

For a code regression, revert the reviewed commit on `main`. The normal `quality` and exact-main deploy path promotes the revert. `workflow_dispatch` with `deploy_production=true` redeploys the current main tip only; it does not pin an older commit. Re-run the bounded public smoke and inspect readiness, error rate, and backend pressure. For a data or Snowflake incident, involve the data owner; API rollback does not repair a changed governed release or view.
