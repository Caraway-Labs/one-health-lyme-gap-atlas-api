# API #233 observation availability investigation

## Finding

The reports are not evidence of a missing SVI/RUCC source-admission decision
or a SQL adapter defect. The public API's 404 is caused by a deliberate
publication boundary: accepted SVI and RUCC measures have source-native
semantic observations in the retained county release, but Data V127's public
county-observation view explicitly allows only three 2023 human measures.
County-profile fields are a separate projection of the same release and are
not sufficient by themselves to manufacture canonical observations.

The public view's omission is intentional under the current temporal contract.
It maps annual periods only. SVI population/percentiles/insurance refer to the
ACS 2018–2022 survey period; RUCC is a 2023 codebook vintage, not an annual
observation. The user's `year=2022` SVI query is also the wrong selector for
that multi-year period. However, correcting only the request year cannot
resolve the 404: these measures are absent from the published view for every
year. The RUCC `year=2023` request would misstate a codebook vintage as a
calendar-year observation.

## Availability and vintage matrix

| Measure | Source/semantic status | Retained release evidence | County-profile field | Public observation view/API status | Correct temporal semantics / next action |
| --- | --- | --- | --- | --- | --- |
| `population_2022` | CDC/ATSDR SVI 2022 accepted source; `E_TOTPOP` → `population_2022` mapping is `EXISTING_REUSED` | Human-run PROD evidence for release `governed-2026-09-18-unknown-coverage`: 3,144 `OBSERVED`; same SVI source version/run/artifact is listed in the 2026-10-06 release manifest. This is retained evidence, not a new query of the 2026-10-06 release. | `population` | Profile is reported in the issue audit; API observation 404 follows V127's allowlist omission. | ACS estimate period 2018-01-01 through 2022-12-31; use an explicit `PERIOD` representation and date-range selector. Data/API owners must govern projection and public exposure. |
| `svi_percentile_2022` | CDC/ATSDR SVI 2022 accepted source; `RPL_THEMES` → `svi_percentile_2022`, percentile 0–1 | Same retained PROD evidence: 3,143 `OBSERVED`, 1 `ZERO`; same source tuple in 2026-10-06 manifest. | `svi_percentile` | Profile is reported in all 300 audit counties; not projected by V127. | ACS 2018–2022 `PERIOD`; preserve national county percentile meaning and zero. Do not query as a single 2022 annual observation. |
| `uninsured_percentile_2022` | CDC/ATSDR SVI 2022 accepted source; `EPL_UNINSUR` → `uninsured_percentile_2022`, distinct from percent | Same retained PROD evidence: 3,143 `OBSERVED`, 1 `ZERO`; same source tuple in 2026-10-06 manifest. | `uninsured_percentile` | Not projected by V127. | ACS 2018–2022 `PERIOD`; retain percentile reference population and zero. |
| `uninsured_percent_2022` | CDC/ATSDR SVI 2022 accepted source; `EP_UNINSUR` → `uninsured_percent_2022`, percent of civilian noninstitutionalized population | Same retained PROD evidence: 3,143 `OBSERVED`, 1 `ZERO`; same source tuple in 2026-10-06 manifest. | `uninsured_percent` | Profile is reported in all 300 audit counties; not projected by V127. | ACS 2018–2022 `PERIOD`; preserve its civilian noninstitutionalized denominator, distinct from `E_TOTPOP`. |
| `rucc_2023` | USDA ERS RUCC 2023 accepted source; `RUCC_2023` attribute mapping exists. Reviewed semantic v2 uses `VINTAGE_YEAR`. | Human-run PROD evidence for release `governed-2026-09-18-unknown-coverage`: 3,144 `OBSERVED`; same RUCC source version/run/artifact is listed in the 2026-10-06 release manifest. This is retained evidence, not a new query of the 2026-10-06 release. | `rucc_2023` | Profile is reported in all 300 audit counties; not projected by V127. | `VINTAGE_YEAR=2023`; never encode as a 2023 annual period or invent a day. Requires a reviewed additive public temporal representation. |
| `state_unallocated_records_2023` | County release slot, but state-scoped meaning; not a county-native observation admission | No county-native measure contract in the V127 view. | Present on county profile | Do not promise as a county observation. | Requires a separately reviewed state-grain semantic/API contract, not a county adapter extension. |
| `pacificus_status` | Tick source field, but API #88's observation admission is limited to its reviewed measure identities | Current API #88 contract admits `scapularis_status` and `burgdorferi_status`; it does not admit `pacificus_status`. | Present on county profile | Not a currently admitted canonical CDC measure. | Do not widen the CDC allowlist from audit failures; source/measure admission needs its existing owner decision. |

The SVI/RUCC source contracts and historical release evidence are in Data
commit [`93085a2`](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/tree/93085a246b7962a9bad1a3f095fb5630bdc1f08d):
[source-mapping matrix](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/docs/contracts/semantic-domain/story-192-source-mapping-matrix-2026-09-24.md),
[V127 observation projection contract](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/docs/contracts/semantic-release/current-county-observations-v1.md),
[DATA #199/#200 PROD reconciliation](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/docs/contracts/semantic-domain/data-199-200-prod-authority-reconciliation-2026-10-03.md),
[reviewed metadata handoff](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/docs/contracts/semantic-domain/data-199-200-metadata-review-handoff-2026-10-04.md),
and the [2026-10-06 release manifest](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/docs/contracts/semantic-release/governed-2026-10-06-nlcd-demo-cohort-manifest.json).
The historical PROD counts are operator-run, read-only evidence for the pinned
2026-09-18 release. The newer manifest shows the same approved source tuples
in a later release; it is not itself a fresh per-measure aggregate or API
response. The issue's 300-county audit summary is separately owner-supplied;
its raw CSV/JSONL were not attached and were not independently inspected here.

The retained 2026-09-18 bundle SHA-256 is
`038aa3f8c383a70699aff92c752f2bbcc6687a726d0c2f142c9f368841b42026`. Its
source tuples are SVI version `b8b6bf61-c6a3-4538-b0df-1b88c61720b1`, run
`331f367d-9282-4936-9b49-a4807377fa7`, artifact
`cdc_atsdr_svi_2022_county:dc042342a2e5abc108af67b439ec04c8` (SHA-256
`dc042342a2e5abc108af67b439ec04c86da8b61f9bdd1cf1b1e5ee0462dea7b2`), and
RUCC version `87872b36-93ab-4a34-b70e-29569192cb48`, run
`f65bd68a-27de-4ad4-81a5-424ee24af3b6`, artifact
`usda_ers_rucc_2023:ec455ee2a8bc5fc8e070575ea5bee7dc` (SHA-256
`ec455ee2a8bc5fc8e070575ea5bee7dce46fc6037f8c3449cbf56e8b45331fa7`). The
source/record reconciliation reports exact manifest, completed-run, artifact,
and historical conformed-record correspondence for the 15,720 selected
semantic observation links. Separate reviewed metadata evidence classifies
all five measure envelopes `INTERNAL`. #193 end-to-end validation remains
unaccepted because representative live authority rows were unavailable to the
authorized audit role; do not treat source/record correspondence as a #193
PASS.

The Data handoff records historical [release build
receipt](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/actions/runs/35328355391)
and [PROD publication receipt](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/actions/runs/35362701191)
for that same release/bundle. These establish the governed release promotion,
not a public API view publication or current-head HTTP check.

All five #191 metadata envelopes remain `INTERNAL` in the reviewed packet.
The Data #194 consumer contract says a reviewed `CONSUMER_SAFE` or `PUBLIC`
classification is necessary but never by itself grants HTTP publication. Data
#194 and API #52/#53 coordination own any later approved consumer projection
and discovery/API contract. The exact next action is for the Data semantic
release/publication owner and API contract owner to approve a bounded V127 (or
successor) projection with SVI `PERIOD` and RUCC `VINTAGE_YEAR` semantics,
then review whether/how these measures transition from `INTERNAL` to
consumer-safe/public visibility. Until that decision and least-privilege
presentation view exist, API #233 should report intentional public
non-exposure, not add a profile fallback. The upstream source registrations
and observations already exist; source-admission change is not the needed
action.

## API contract and regression boundary

The observation repository probes and queries only
`PRESENTATION.CURRENT_COUNTY_OBSERVATIONS_V`. Its SQL has no county-profile
fallback. V127's contract allows `human_status`, `case_count_floor_2023`, and
`incidence_floor_2023` only, with annual 2023 periods. The API measure catalog
may describe known measure metadata; catalog membership does not assert public
observation availability. A 404 from the observation endpoint is therefore
the expected result when the requested measure has no row in the current
public observation view.

The public V1 observation model currently requires a start/end period. It can
represent the SVI survey window with the exact dates once a governed public
projection exists. RUCC needs an additive, explicit vintage-year semantic; a
calendar-year date pair would be false precision. Any public schema/view
extension needs OpenAPI, compatibility, and Web client review. No adapter SQL,
source admission, or public contract change is made in this evidence update.

## Remaining production gate

The original task requested a live production verification. This environment
could not reach the public API or GitHub REST/GraphQL through its network proxy,
so no fresh catalog, source view, production reader, or HTTP response was
queried. Existing retained evidence establishes approved source tuples and
historical semantic rows, but not the live API's exact current release response
or whether a new public temporal contract has been approved. Do not claim
production UAT from fixtures or manifests.
