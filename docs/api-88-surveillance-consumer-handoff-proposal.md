# API 88 surveillance evidence consumer handoff proposal

Status: **proposal for Data and API owner review**. This document does not authorize a public route, a producer publication, or acceptance of live evidence. DATA 429's canonical CDC/NEON bundle and source-authority proof are still required.

## Existing boundary

The public observation service reads `CURRENT_COUNTY_OBSERVATIONS_V` through `SnowflakeObservationRepository` and maps its 28 selected columns to `Observation`. It selects a pinned current release, a single measure, county FIPS, and a bounded period. That projection contains neither the DATA 634 `atlas-surveillance-evidence-consumer-v2` envelope nor a producer `revision_id`. The API cannot join a companion to it by timestamp or by a guessed county/period key. DATA 634 already owns strict envelope serialization; API 88 should consume that contract rather than define another scientific classification.

## Proposed delivery and admission seam

1. Data publishes a reviewed, consumer-safe **delivery contract** for the existing observation repository. It can be a governed relation or another bounded repository result; the physical form is Data-owned. Each row supplies the existing public observation columns plus a complete serialized DATA 634 envelope, a stable observation/revision pair, and an explicit release binding. Data documents how the pair joins the current release and the existing `observation_id`. A new SQL view name is not assumed here.
2. API adds a typed, disabled-by-default repository adapter behind `ObservationService`, leaving the current route and 28-column reader intact until the delivery contract is reviewed and populated. The adapter validates the envelope against the Data-owned versioned schema, then requires equality of the envelope's semantic and surveillance companion observation key, revision ID, and value state. It also requires equality to the delivery row's observation/revision pair and release, and verifies source/version authority from the reviewed Data ledger binding. A missing, ambiguous, mismatched, private, or unreviewed binding fails closed for that row/release; it never falls back to the old row as apparent absence.
3. Data records the approved source rule, source version, ledger joins, and stewardship decision for every admitted release. A fixture or an `INTERNAL` lineage is ineligible for the public adapter even if it is schema-valid. The API only projects reviewed consumer-safe fields. It does not expose raw lineage IDs, proof hashes, canonical source rows, private notes, or a new evidence-state engine.

## Initial supported slice

The first integration should cover only a reviewed county, measure, period, and temporal grain that the existing `ObservationQuery` can select without aggregation. Data and API must name those exact values in the delivery contract and confirm their public `ValueState` mapping before enabling the adapter. The API's present public enum is `OBSERVED`, `ZERO`, `MISSING`, `SUPPRESSED`, `UNAVAILABLE`, and `NO_COUNTY_LINKED_RECORD`; the DATA 634 semantic vocabulary is broader. `UNKNOWN`, `NO_RECORDS`, `NOT_REPORTED`, and `NOT_DEFENSIBLE` must not be coerced into an existing public state. Unsupported grain, geography, value state, or missing mapping stays unavailable with an explicit admission error; `no_qualifying_record` never implies zero, absence, or `NO_COUNTY_LINKED_RECORD`.

## Comparison and alignment companions

DATA 430 methodology-era/comparability and DATA 431 lag/alignment are separate proposed governance outputs. When reviewed, Data should deliver each as an optional companion bound to the **same observation key, revision ID, source/version authority, and release** as the evidence envelope. API validates those identities before attaching reviewed, consumer-safe context to `Observation` or its metadata. Missing companion means no comparison/alignment claim. `UNKNOWN_AVAILABILITY` is not inferred from processing time; `NOT_COMPARABLE` and `RETROSPECTIVE_ONLY` cannot become an ordinary trend comparison. API never derives evidence state, methodology era, or lag from dates, timestamps, or neighboring observations.

## Offline acceptance before activation

- Contract fixture covers all five DATA 634 evidence states and strict version/field validation using the Data-owned schema; no second API taxonomy.
- Mismatched semantic/evidence/row observation key, revision ID, value state, release, source ID, or source version rejects admission. Duplicate pairs, absent ledger proof, private fields, and an `INTERNAL` nonfixture envelope reject admission.
- Fixture evidence cannot reach public serialization. Unsupported public value state/grain and unavailable comparison/alignment companions produce no false absence, zero, or comparability claim.
- Once the delivery contract and safe projection are approved, add public DTO/OpenAPI and route tests, including documented unsupported and unavailable behavior. Keep the adapter disabled until DATA 429 provides genuine records and reviewed authority, and then require a separate live reader proof for the intended release.

This proposal deliberately leaves the physical delivery relation, supported slice, public DTO shape, and release decision to the joint Data/API review. They must be specified before implementation claims an integrated public consumer.

