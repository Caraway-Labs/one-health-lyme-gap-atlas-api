# API #88 evidence-state integration

Historical PR #224 receipt. DATA #429 has since completed. The current
API-owned CDC integration and remaining runtime acceptance gates are in
[production CDC integration](api-88-production-cdc.md); its closed Data
dependency is not reopened by this historical audit.

This increment lets the existing observation service serialize already admitted
Data evidence and temporal companions. It adds no classifier, source registry,
SQL view, ingestion, grant, release mutation, endpoint or production resolver.
The production app still constructs the service without a surveillance resolver.

## Contract and existing code reused

`Observation.surveillance_context` is optional. Its evidence companion adds
optional `normalized_state`: `established`, `detected`, or `unknown`. The API
copies this field from DATA #429's `project_surveillance_evidence` output; it
never derives it from `state`, a publisher label, or missing values. An older
companion without normalization remains valid and yields null, with no scientific
conclusion. An explicitly supplied unsupported normalized token or null in the
Data envelope fails strict validation. Unknown supports no biological absence.
The publisher value remains `Observation.value`, verified against the envelope's
semantic observation; source, geography, period, value state and provenance remain
unchanged. The historical `state` remains available for compatibility.

The copied evidence schema matches Data's additive schema at
`641620ddc65a8183d45d2c5c0522ca3a92f5a265`, including merged PR #670.
`additionalProperties: false` and the Pydantic `extra="forbid"` boundaries remain.
Public and first-party OpenAPI are generated from these models. Web client
regeneration belongs to its owning repository before a consuming UI release;
no web checkout is changed by this API PR.

`ObservationService` and `create_app` accept an optional trusted
`SurveillanceResolver`. When supplied, every returned observation passes through
the existing `resolve_context` / `admit_surveillance_context` seam from PR #211
and the supported-slice enforcement from PR #221. A configured resolver must
support the selected query slice; unsupported observations reject the request,
rather than silently omitting a failed companion. Supplied delivery errors use
the existing unavailable/503 path. No public parameter or environment setting
selects a resolver or asserts admission. The default service retains its existing
data reader and supplies null surveillance context.

DATA #430 comparison and #431 alignment projections reuse existing DTOs and
independent bindings. The service makes no pair selection, era classification,
lag choice, rescaling, or comparability inference. Missing companions remain
null. `COMPARABLE` concerns case-definition scope only; alignment eligibility
does not authorize causal interpretation or cross-era pooling.

## Acceptance and invariant audit

| Requirement | Implementation / evidence | Status |
| --- | --- | --- |
| Additive normalized-state acceptance | Copied schema, evidence DTO; actual pinned Data writer outputs validate | Offline verified |
| Preserve established/detected/unknown | Direct DTO projection, HTTP tests for all three; no API mapping | Offline verified |
| Missing/unsupported evidence never means absence | Missing normalization null, Data unknown retained, unsupported tokens denied; no synthesized observation | Offline verified |
| Preserve publisher label | Existing semantic value equality; HTTP `Reported` preserved | Offline verified |
| Backward compatibility | Old envelope accepted without normalization; optional observation field; legacy state retained | Offline verified |
| Release/bundle/source/revision authority | Existing exact binding and identity checks reused; negative tests include stale release, hash mismatch and missing authority | Offline verified |
| INTERNAL and privacy rejection | Strict schema rejects private visibility; unmodified synthetic envelopes rejected; Data nonfixture INTERNAL remains denied upstream | Offline verified; live admission blocked |
| Supported surveillance slice | PR #221 county/measure/source/grain/period/value-state restrictions unchanged; all three families covered | Offline verified |
| Methodology and alignment | Existing DATA #430/#431 producer binding checks; HTTP unknown/not-comparable and declared lag passthrough | Offline verified |
| Public and first-party contracts | Both exports include optional strict context and narrow normalized vocabulary | Offline verified |
| Genuine intended-reader PROD consumer acceptance | No admitted nonfixture delivery exists; no fixture receipt substituted | Blocked on DATA #429 |
| Scientific/steward review and public admission | No approval or activation added | Remains required |

## Exact Data dependency and next action

DATA #429 remains open. Its 2026-10-11
[PROD receipt](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/issues/429#issuecomment-6111155203)
records applied V148, intended-role restricted audit readback, genuine CDC
lineage, and helper probes using verified publisher labels. Those are upstream
receipts, not a live consumer replay performed by this API change.

Inspection of pinned Data code identifies the unmet predicate precisely:
`semantic_consumer.project_consumer` requires
`lineage["visibility"] == "CONSUMER_SAFE"`. `semantic_source_mappings.map_record`
only emits that visibility for the separately approved climate method
`atlas-nclimgrid-county-day/2` with consumer-safe metadata and an approved
metadata revision; nonfixture CDC tick/pathogen mappings emit `INTERNAL`.
The consumer raises `SemanticConsumerError("lineage is internal")`. Metadata
visibility and `steward_review.state == "REVIEWED"` are separate necessary
predicates. The consumer envelope does not carry private visibility or a public
admission decision; schema validity cannot establish either.

The smallest Data-owned next step under #429 is a reviewed, bounded admission
of the exact existing CDC mapping/source/version/metadata/release slice through
its current mapper and consumer boundary. It must preserve source approval,
ledger reconciliation and formal steward review, without a blanket visibility
change or use of fixture mode. Data must then supply its independently verified
release/bundle authority and exact observation/revision/source joins through the
agreed delivery binding, and demonstrate nonfixture intended-reader readback.
No missing-source parity placeholder may be given invented source lineage.

DATA #430 and #431 issues are closed; their implementations are available, but
closure alone does not establish reviewed public delivery or live availability.
After genuine Data consumer admission succeeds, the API can configure the trusted
resolver for the approved slice and perform end-to-end intended-role verification.
That resolver/activation must be reviewed separately against the actual Data
delivery contract. Do not deploy or close #88 on this increment's fixture results.

## Deployment and rollback

This PR does not merge or deploy. API main's successful quality workflow deploys
production, so human review must account for that behavior before merging.
The default runtime remains inactive for surveillance companions. There is no
migration or backfill. Rollback is the preceding reviewed API commit; removing
the optional resolver disables delivery without altering Data state. Consumers
must tolerate absent/null context and normalization and retain interpretation
limitations. Accessibility remains the existing structured observation response;
no map-only or UI behavior is introduced.
