# API #88 production CDC integration

## Scope and authority

DATA #429 is completed; no Data review or publication is reopened.
The API pins its accepted producer artifact at
`8ce5d47d518161986d20954f3ddceb8f66504c92` and consumes the exact `_project`
producer and `_resources` used by `cdc_consumer_runtime.read_cdc_consumer`.
The convenience reader hardcodes a 30-second transport timeout; this API adapter
supplies only the query callback through its existing governed connection with
`min(public_query_timeout_seconds, 30)` on every statement and the session.
Default is 15 seconds; a configured 5-second bound reaches both identity and
authority reads. Data retains all classification, metadata and admission logic.
No Data module is presumed present: the dependency is installed through the
existing uv lock and Docker build. Canonical metadata is packaged by Data.
No scientific inference or duplicated private authority SQL is introduced.
Public `source_id` and `evidence.source_id` use the accepted manifest's catalog
keys `tick` and `pathogen`, resolving through `/v1/sources/{source_id}`. They are
distinct from `lineage_source_id=cdc_arbonet_tick_module`, the two dataset IDs,
and Data's private ingestion resource/version identities. The producer retains
all private resource admission checks; these keys do not broaden admission.

Release: `governed-2026-10-06-nlcd-demo-cohort`.
Bundle: `603afb135a1a5c958d1d56d48d580d9942c67eb38986caf8b4b3acf69fcde2c9`.
Source/version/metadata bindings are pinned in `cdc_surveillance.IDENTITIES` and
validated again by the existing resolver admission seam. The producer checks
retained observation membership on each read; query success is not admission.

## Service access and release gate

Live DigitalOcean app configuration inspected 2026-10-11:
`OH_LYME_API_SVC` / `OH_LYME_PROD_READ` / `COMPUTE_WH` /
`ONE_HEALTH_LYME_GAP_ATLAS_PROD`, authentication `key_pair`.
Private key and passphrase are present as encrypted runtime secrets. No secret
was extracted. Local service credentials are unavailable. The operator
`ATLAS_PROD_READ` read verified those role/database/warehouse settings and the
three authoritative rows, but its user is MATTHEWCARAWAY; that is not service-key
proof. The installed pinned Data producer also succeeded for tick 13081 using
that operator connection; this does not establish API production acceptance.

Live read-only grant inspection confirmed `OH_LYME_API_SVC` holds
`OH_LYME_PROD_READ`; that role has database/schema/warehouse USAGE and SELECT
on the exact lineage view. No missing grant was identified and none was issued.

The local DigitalOcean console attempt failed (`error getting terminal size:
The handle is invalid`). Actual in-container service reads remain a release
gate. After independent review and human merge, the protected existing main
workflow must deploy the exact reviewed SHA and report ACTIVE.

Run inside the deployed API component:

```sh
python -m lyme_gap_atlas_api.cdc_reader_probe
```

It verifies actual current identity then performs the three producer reads;
only service identity and county/measure/normalized-state summaries are emitted.
Data supplies a reviewed measure definition and methodology version, but no
distinct source label or public methodology catalog ID/text. Those nullable
observation fields remain null; a measure label is not source attribution and a
version is not a methodology resource ID. Known source/vintage, transformation
and methodology versions, metadata revision and individual limitations remain.
The API transport disables secondary roles in each service session, and the
pinned Data producer checks that session's role/database/warehouse before any
authority read (`cdc_consumer_runtime.py:293-301`). A regression test
exercises that actual packaged producer against a wrong session role. The probe
also redacts connection/producer/shape failures with a fixed exit message and
emits no partial acceptance output or private traceback.
After activation, run `uv run python scripts/probe_cdc_api.py` from this reviewed
checkout and retain its sanitized genuine HTTP output on API #88.
Do not substitute a human PAT. Minimum required access: assignment of the
existing OH_LYME_PROD_READ role to OH_LYME_API_SVC, USAGE on COMPUTE_WH and the
PROD database and LINEAGE_AUDIT schema, and SELECT on
LINEAGE_AUDIT.SEMANTIC_LINEAGE_AUDIT_V (plus existing presentation current-release
read access). No new grant is included or authorized. If unavailable, use Data's
existing `docs/operations/prod-role-consolidation-runbook.md` and its protected
security provisioning procedure with owner authorization; do not elevate here.

Once actual service access passes, set `CDC_SURVEILLANCE_ENABLED=true` in the
existing API runtime environment, deploy through the protected procedure, and
verify actual SHA/ACTIVE/configuration again. Default false keeps deployment
separate from activation. Disable the same flag for activation rollback.

## HTTP acceptance and provisional Web instructions

Endpoint: `GET https://api.onehealthatlas.org/v1/observations`.
Common parameters: `geography_type=county`,
`start_date=2025-12-31`, `end_date=2025-12-31` (no `year`).

| measure_id | geography_id | Original value | Canonical value_state | Data normalized_state |
| --- | --- | --- | --- | --- |
| scapularis_status | 13081 | Reported | OBSERVED | detected |
| burgdorferi_status | 19135 | Present | OBSERVED | detected |
| burgdorferi_status | 12047 | No records | NO_RECORDS | unknown |

Use bare measure IDs above; `1.0.0` is semantic version, not an ID suffix.
Only these retained memberships are enabled. Other CDC counties, annual time
coercion, failed producer admission or credential failures return the existing
503 unavailable problem, without fabricated evidence. Other existing measures
retain their existing repository path. Pagination binds release and parameters.

For each admitted request verify HTTP 200, original value/value_state, the
expected `surveillance_context.evidence.normalized_state`, exact reviewed
`metadata_revision_id`, release, cumulative period and both sets of limitations.
Confirm no source-version IDs, bundle proof, authority reference, raw record
references or private lineage are serialized. Probe an unsupported CDC county
and expect 503. Save sanitized genuine response examples only after acceptance;
offline fixtures are not HTTP production evidence.

Web must regenerate its TypeScript client from this PR's committed OpenAPI
before consuming `NO_RECORDS` and optional `metadata_revision_id`.
Display the publisher label alongside the Data normalized state; unknown means
no biological conclusion, and unavailable means evidence could not be delivered.
Show cumulative-through-2025-12-31 and limitations; do not label these annual
sampling, current ecology, absence, prevalence or disease risk. No Web code is
changed here. Genuine HTTP examples and final Web handoff remain pending the
production acceptance gate; API #88 must remain open until that gate passes.

## Acceptance audit

| Criterion | Evidence |
| --- | --- |
| Exact Data package / resolver reuse | Dependency pin and focused binding tests |
| Three admitted observations / canonical values | Focused offline service/HTTP tests |
| Admission, privacy, unavailable, compatibility | Negative tests and public compatibility floor |
| OpenAPI | Generated public and first-party documents; contract tests |
| Actual service-key authorization | Pending in-container probe |
| Protected deployment and activation | Pending independent review and human merge |
| Genuine production HTTP acceptance | Pending deployment/activation |
| Final examples, Web handoff, issue closure | Pending genuine HTTP acceptance |
