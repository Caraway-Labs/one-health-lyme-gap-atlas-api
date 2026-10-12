# API #229 — executable County Review v2

The existing Review endpoint now runs `atlas-county-review@2.0.0`, with all four
rules enabled, real evidence loading, per-county decisions and per-rule counts.
Historical v1.0.0's config and engine remain unchanged. The active config is a
packaged immutable JSON artifact whose byte hash is returned in each response.
Automatic review stages remain disabled.

This is an implementation PR, not a readiness-only report. It has not been
merged or deployed. **Production is still on v1.0.0** until an explicitly
authorized release. Scientific comparison thresholds and expanded CDC admission
are not invented as a side effect of enabling execution.

## Execution paths

| Rule | Executed checks | Current supported outcome |
|---|---|---|
| human_emerging | Load published 2023 county floor, source, value state and period; assess usable values and comparable baseline; reject directional conclusions from unbounded floors | Missing/suppressed/unavailable values abstain; a positive floor without history abstains with `NO_COMPARABLE_HISTORY` |
| vector_transition | Inspect admitted county-native taxon/target observations and published cumulative context; check for same-target prior snapshots and Data comparability | Single cumulative vintage abstains with `NO_COMPARABLE_NATIVE_SNAPSHOTS`; no transition is invented from No records |
| human_vector_discordance | Require independently observed human and admitted vector/pathogen families, then compare periods/grains | Missing paired evidence or annual/cumulative mismatch abstains; matching periods still require a reviewed materiality/adequacy criterion |
| pathogen_present_vector_reported | Species-specific published Established fact can disprove the categorical predicate; otherwise existing native D evaluator validates all required evidence | Established species supports evaluated/noncandidate results; incomplete native pairs abstain; only complete eligible native tuples can qualify |

The loader batches up to 200 human counties per existing bounded query and only
queries CDC memberships already supported by the adapter. It does not query
restricted raw workbooks or widen admission. Source/release/measure/geography/
period mismatches, duplicate rows and unexpected pagination fail the batch closed.
Transport/admission failure is recorded as `EVIDENCE_DELIVERY_UNAVAILABLE`, not
zero or publisher No records. No baseline window is selected without approval.

New `county_evaluations` expose rule IDs, eligibility, evaluated/abstained/candidate
booleans, reason codes and public evidence. `rule_evaluation_counts` supplies
independent counts. The original coverage fields and result-state enum remain.
Evaluated and abstained county totals may overlap because different rules can
have different outcomes for the same county. Eligible means admitted to the
assessment universe; it does not assert complete scientific support.

Public evidence retains county/release, safe record reference, source label/URL/
vintage, measure, original value/state, observation period/grain, known retrieval
timestamp, method version, metadata revision and limitations. Unknown dates
remain null. A published county fact is explicitly identified as such; it is not
represented as an admitted native observation. Candidate references preserve
the previous shape but never emit private source-version UUIDs, raw row IDs or
private authority/proof hashes.

## Production-input integration evidence

Fresh checks on 2026-10-11 America/Denver (2026-10-12 UTC) used named PAT
`ATLAS_PROD_READ`, identity verified as MATTHEWCARAWAY / OH_LYME_PROD_READ /
ONE_HEALTH_LYME_GAP_ATLAS_PROD / COMPUTE_WH before SELECTs. Read 226 current
published county records scoped to AL/GA, excluding geometry payloads, and
captured public HTTP human observation collections for both states plus the
already admitted scapularis observation for 13081. No private rows were exported.
These real inputs were passed through the candidate FastAPI Review route using
TestClient and the production loader, with a captured ObservationService boundary.
This verifies actual rule behavior on production evidence; it is **not** deployed
candidate code, an actual service-key runtime test or production latency proof.

Release: `governed-2026-10-06-nlcd-demo-cohort`. Human inputs: CDC x5j9-wybp,
2023 annual privacy floors. Tick inputs: published cumulative-through-2025-12-31
species facts and the admitted CDC scapularis membership for 13081.

| Candidate route | HTTP | Method | Assessed | Evaluated (D) | Abstained (any rule) | Candidates | Result |
|---|---|---|---:|---:|---:|---:|---|
| /v1/states/AL/review | 200 | 2.0.0 | 67 | 36 | 67 | 0 | insufficient_evidence |
| /v1/states/GA/review | 200 | 2.0.0 | 159 | 39 | 159 | 0 | insufficient_evidence |

A/B/C each abstain for all counties under current source support. D evaluates
36 AL and 39 GA counties as nonqualifying because an Established species
disproves its predicate; D abstains for the remaining 31 AL and 120 GA counties.
All rule coverage values are `enabled`.

Representative real-input outcomes:

- Baldwin 01003: human value 5 / OBSERVED / annual 2023 / original retrieval
  2026-09-07. A abstains because no comparable baseline exists. Published
  scapularis Established supports D's evaluated nonqualification.
- Autauga 01001: human numeric value null / MISSING, never zero; A abstains.
- Crisp 13081: genuine public CDC Reported / OBSERVED / normalized detected /
  CUMULATIVE 2025-12-31. Retrieval timestamp is null. No admitted pathogen plus
  both-vector pairing is supplied; D abstains rather than promoting summary
  fields or manufacturing a date.

Sanitized receipts and selected full county outcomes are committed in
[`evidence/api229/production-input-canary.json`](evidence/api229/production-input-canary.json).
The complete safe input/response captures, bounded SQL and replay script are
retained in `.local/api229/` in the isolated worktree, not committed. Current
public API #88 successes and its separate 503-to-504 ingress defect do not
establish candidate activation. The lineage/admission work accepted in DATA #429
is preserved; no new Data source or permission action is included.

## Compatibility and validation

The `StateReview` schema name, operation ID, existing required fields, legacy
candidate/reference shape, enum, dataset_version mismatch handling and cache
headers remain. New evaluation fields have defaults and are optional in OpenAPI.
First-party OpenAPI is regenerated alongside public OpenAPI. Validate the old Web
Zod reader against AL/GA candidate results, then regenerate and typecheck current
Web in a disposable archive inside this isolated worktree; no Web checkout is
modified. Tier1 code/contracts are untouched.

Required commands: `uv sync --extra dev --locked`, `uv run ruff check .`,
`uv run mypy`, `uv run pytest -q`, `uv run python scripts/export_openapi.py`,
`uv run python scripts/openapi_quality.py --base-ref origin/main`, and
`docker build .`. The OpenAPI quality command compares against committed HEAD,
so run it after committing the regenerated contract. Existing v1 tests continue
to prove disabled configuration and deterministic fixture behavior; v2 tests
cover active configuration, value states, history/period/comparison gates,
nonqualifying D evidence, positive native D fixture, missing/lineage/source/
release errors, count aggregation, atomic loader admission and error separation.

Synthetic complete D fixtures test the existing predicate; no real positive
cohort is claimed. A/B/C cannot have legitimate positive fixtures until approved
comparators exist; tests explicitly require abstention instead of inventing them.

Local Docker could not connect to DockerDesktopLinuxEngine. Hosted PR quality
must supply Linux container/production Typst/secret-scan evidence; this is an
execution limitation, not a container pass. Final test counts, Web verification
and independent final-SHA review are recorded in the PR/issue after execution.

## Remaining data and policy gaps

Current source support lacks an admitted comparable human baseline, bounded
floor-change comparator and approved lookback/support/freshness; comparable
historical county-native vector snapshots and transition policy; aligned
independently interpretable human/vector pairs with approved adequacy/materiality;
and a complete same-county admitted D tuple including pacificus and safe dates.
No arbitrary scientific parameters, biological absence or risk interpretation
are substituted for these gaps. Future approved comparative candidate logic
requires a new immutable methodology version. Existing scope does deliver
enabled, transparent assessment and supported D nonqualification today.

See [ADR 0018](adr/0018-enabled-county-review-assessment.md) for the proposed
method and nonqualification admission decision. Independent peer review and
explicit owner merge/deployment approval remain mandatory.
