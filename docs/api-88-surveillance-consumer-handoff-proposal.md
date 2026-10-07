# API 88 surveillance consumer handoff proposal

Status: **joint Data/API review proposal**, not a publication or admission decision. API 88 remains open through evidence-state, comparison, alignment, OpenAPI, and live acceptance. The initial county slice below is only a proposed partial increment.

## Existing boundary and producer authority

`ObservationService` reads a pinned release through `SnowflakeObservationRepository` and maps 28 `CURRENT_COUNTY_OBSERVATIONS_V` columns to `Observation`. Those columns have no DATA 634 envelope or producer revision ID. A timestamp, county/year, or `observation_id` guess cannot join a companion safely. The current reader remains in place until Data publishes an agreed delivery binding; no SQL view is asserted to exist.

Data's existing authoritative evidence entrypoint is `surveillance_evidence_mapping.project_surveillance_evidence(record, metadata, authority, mappings, fixture_mode=False)`. It calls `semantic_consumer.project_consumer` and enforces its lineage, visibility, authority, and nonfixture steward-review gate. The API cannot recreate that gate from JSON Schema or from the envelope: the envelope has no admission/visibility field. Data must invoke the projector with its actual authority and ledger inputs, then attach an independently verifiable delivery/admission record. No caller-controlled approval boolean, API scientific classifier, or substitute ledger engine is proposed. The current nonfixture DATA 429 mapping remains INTERNAL and denied. Synthetic `fixture_mode` output is test-only.

## Typed delivery shape for agreement

The delivery transport may be a governed relation or bounded repository result owned by Data. The API adapter receives these typed logical records, never raw producer inputs:

| Family | Required private delivery binding | Public-safe payload from existing projector |
| --- | --- | --- |
| Evidence | `release_id` (non-null), `bundle_sha256`, `observation_key`, `revision_id`, `source_id`, `source_version_id`, existing public `observation_id`, measure/geography/period/grain, admission record ID | DATA 634 `atlas-surveillance-evidence-consumer-v2` envelope from `project_surveillance_evidence` |
| Methodology comparison | Same release and bundle binding; **left and right** observation key, revision, source ID and source version; comparison context (jurisdiction, case category, reviewed applicability) | DATA 430 `project_methodology_comparison` output |
| Temporal alignment | Same release and bundle binding; input observation key, revision, source/version; method ID/version, observation and target windows, decision cutoff, applicable reference-period identity/version | DATA 431 `project_temporal_alignment` output |

The binding is an internal admission carrier, not a public DTO. Data must specify its exact field names, authority record, bundle SHA-256 derivation, and join to the existing repository/release. The adapter checks non-null `release_id` and exact `bundle_sha256` against Data's admitted release record, then exact identity matches between delivery binding and the existing projector payload or its Data-certified projection proof. It also checks the evidence envelope's semantic/evidence observation key, revision, and value state equality. A schema-valid document alone is insufficient. Data 430 and 431 can arrive independently; neither requires a DATA 429 envelope. Do not publish private source versions/revisions, authority IDs, or proof hashes in the public safe projection.

## Supported slice and unavailable behavior

Data and API must name an explicit allowlist of measure, source, geography, temporal grain, value state, and period semantics before enabling the adapter. A county annual observation is supportable only when the source actually describes that annual county phenomenon. Cumulative CDC tick status is not annual; NEON site/event negatives are not county observations. The API's current public states are `OBSERVED`, `ZERO`, `MISSING`, `SUPPRESSED`, `UNAVAILABLE`, and `NO_COUNTY_LINKED_RECORD`. Data's `UNKNOWN`, `NO_RECORDS`, `NOT_REPORTED`, and `NOT_DEFENSIBLE` must remain distinct until a reviewed public DTO mapping can represent them losslessly. `no_qualifying_record` is never zero, sampled negative, absence, or `NO_COUNTY_LINKED_RECORD`.

An absent admitted bundle, failed admission, missing/duplicate or mismatched identity, unsupported grain/state, or inaccessible delivery result makes that requested evidence **unavailable** under the API's existing 503 `AtlasDataUnavailableError` path; it does not synthesize a row, zero, or no-records claim. A genuinely absent supported resource can retain the existing 404 behavior only after the admitted release establishes that absence. The current public reader is not silently replaced or backfilled by an ineligible bundle. Comparison/alignment omitted from a valid admitted observation carries no comparability, eligibility, lag, or availability claim; errors in a supplied companion reject that companion/release rather than degrade it to an affirmative state.

## Offline acceptance before activation

- Inject synthetic producer fixtures through a test-only repository to cover all five DATA 634 evidence states, strict schema/version/privacy serialization, service response, and eventual OpenAPI shape. Fixtures never enter the production reader and cannot prove live acceptance.
- Deny swapped DATA 430 left/right identities, stale revisions, changed source versions, unreviewed comparison context, mismatched DATA 431 declaration inputs/method/windows/cutoff/reference period, changed release or bundle hash, absent authority, and duplicate identity pairs.
- Deny unsupported geography, grain, period semantics, and public value states. Preserve explicit unavailable/503 and legitimate 404 behavior; never coerce a cumulative status or site negative into annual county evidence.
- Once Data and API agree on the delivery shape and public safe projection, implement a disabled typed adapter and offline admission/privacy tests without live canonical data. Add public DTO/OpenAPI tests only for the reviewed slice. Activation and final API 88 acceptance require DATA 429 genuine retained CDC/NEON records, matching ledger joins, reviewed metadata, scientific/steward decision, and separate live reader proof.

No producer publication, new endpoint/platform, credential, grant, or query is requested by this proposal.

