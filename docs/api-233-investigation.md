# API #233 observation availability investigation

## Finding

The reports are not evidence of a missing SVI/RUCC source-admission decision
or a SQL adapter defect. The public API's 404 is caused by a deliberate
publication boundary: accepted SVI and RUCC measures have source-native
semantic observations in the retained county release, but the current Data
V138 public county-observation view does not include SVI or RUCC. It retains
the three 2023 human measures and adds seven bounded NLCD 2025 measures for
two counties. County-profile fields are a separate projection of the same
release and are not sufficient by themselves to manufacture canonical
observations.

The current omission follows the projection's allowlist and temporal contract;
it is not a permanent scientific exclusion. SVI population/percentiles/insurance
refer to the ACS 2018–2022 survey period; RUCC is a 2023 codebook vintage, not
an annual observation. The issue's example `year=2022` SVI query does not
represent that multi-year period. Correcting only the selector cannot resolve
the 404 because these measures are absent from the current view. A RUCC
`year=2023` request would misstate a codebook vintage as an annual observation.

## Availability and vintage matrix

| Measure | Source/semantic status | Retained release evidence | County-profile field | Public observation view/API status | Correct temporal semantics / next action |
| --- | --- | --- | --- | --- | --- |
| `population_2022` | CDC/ATSDR SVI 2022 accepted source; `E_TOTPOP` → `population_2022` mapping is `EXISTING_REUSED` | Human-run PROD evidence for release `governed-2026-09-18-unknown-coverage`: 3,144 `OBSERVED`; same SVI source version/run/artifact is listed in the 2026-10-06 release manifest. This is retained evidence, not a new query of the 2026-10-06 release. | `population` | Profile is reported in API #233's 96-county / 1,139-GET production audit; not included in current V138 projection. | ACS estimate period 2018-01-01 through 2022-12-31; use an explicit `PERIOD` representation and date-range selector. |
| `svi_percentile_2022` | CDC/ATSDR SVI 2022 accepted source; `RPL_THEMES` → `svi_percentile_2022`, percentile 0–1 | Same retained PROD evidence: 3,143 `OBSERVED`, 1 `ZERO`; same source tuple in 2026-10-06 manifest. | `svi_percentile` | Separate owner-supplied 300-county / 927-GET workbook reports profile values; not included in current V138 projection. This workbook is distinct from API #233's 96-county / 1,139-GET issue audit. | ACS 2018–2022 `PERIOD`; preserve national county percentile meaning and zero. Do not query as a single 2022 annual observation. |
| `uninsured_percentile_2022` | CDC/ATSDR SVI 2022 accepted source; `EPL_UNINSUR` → `uninsured_percentile_2022`, distinct from percent | Same retained PROD evidence: 3,143 `OBSERVED`, 1 `ZERO`; same source tuple in 2026-10-06 manifest. | `uninsured_percentile` | Not included in current V138 projection. | ACS 2018–2022 `PERIOD`; retain percentile reference population and zero. |
| `uninsured_percent_2022` | CDC/ATSDR SVI 2022 accepted source; `EP_UNINSUR` → `uninsured_percent_2022`, percent of civilian noninstitutionalized population | Same retained PROD evidence: 3,143 `OBSERVED`, 1 `ZERO`; same source tuple in 2026-10-06 manifest. | `uninsured_percent` | Separate owner-supplied 300-county / 927-GET workbook reports profile values; not included in current V138 projection. This workbook is distinct from API #233's 96-county / 1,139-GET issue audit. | ACS 2018–2022 `PERIOD`; preserve its civilian noninstitutionalized denominator, distinct from `E_TOTPOP`. |
| `rucc_2023` | USDA ERS RUCC 2023 accepted source; `RUCC_2023` attribute mapping exists. Reviewed semantic v2 uses `VINTAGE_YEAR`. | Human-run PROD evidence for release `governed-2026-09-18-unknown-coverage`: 3,144 `OBSERVED`; same RUCC source version/run/artifact is listed in the 2026-10-06 release manifest. This is retained evidence, not a new query of the 2026-10-06 release. | `rucc_2023` | Reported by the separate owner-supplied 300-county / 927-GET workbook, not API #233's 96-county / 1,139-GET audit; not included in current V138 projection. | `VINTAGE_YEAR=2023`; never encode as a 2023 annual period or invent a day. Requires a reviewed additive public temporal representation. |
| `state_unallocated_records_2023` | County release slot, but state-scoped meaning; not a county-native observation admission | No county-native measure contract in current V138 view. | Present on county profile | Do not promise as a county observation. | Requires a separately reviewed state-grain semantic/API contract, not a county adapter extension. |
| `pacificus_status` | Tick source field, but API #88's observation admission is limited to its reviewed measure identities | Current API #88 contract admits `scapularis_status` and `burgdorferi_status`; it does not admit `pacificus_status`. | Present on county profile | Not a currently admitted canonical CDC measure. | Do not widen the CDC allowlist from audit failures; source/measure admission needs its existing owner decision. |

API #233 describes a 96-county sample and 1,139 production GETs; its raw
`atlas_county_api_audit.csv`, `atlas_api_raw_responses.jsonl`, and summary are
owner artifacts not attached to the issue, so that audit was not independently
replayed here. The separate owner-supplied 300-county / 927-GET workbook is a
different evidence set; its raw workbook was also unavailable for inspection.
Keep those populations and request counts distinct.

The SVI/RUCC source contracts and historical release evidence are in Data
commit [`93085a2`](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/tree/93085a246b7962a9bad1a3f095fb5630bdc1f08d):
[source-mapping matrix](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/docs/contracts/semantic-domain/story-192-source-mapping-matrix-2026-09-24.md),
[V138 current observation projection](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/migrations/V138__bounded_nlcd_consumer_observations.sql),
[DATA #199/#200 PROD reconciliation](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/docs/contracts/semantic-domain/data-199-200-prod-authority-reconciliation-2026-10-03.md),
[reviewed metadata handoff](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/docs/contracts/semantic-domain/data-199-200-metadata-review-handoff-2026-10-04.md),
and the [2026-10-06 release manifest](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/blob/93085a246b7962a9bad1a3f095fb5630bdc1f08d/docs/contracts/semantic-release/governed-2026-10-06-nlcd-demo-cohort-manifest.json).
The historical PROD counts are operator-run, read-only evidence for the pinned
2026-09-18 release. The newer manifest shows the same approved source tuples
in a later release; it is not itself a fresh per-measure aggregate or API
response. The two audit summaries above are owner-supplied and were not
independently inspected here.

The retained 2026-09-18 bundle SHA-256 is
`038aa3f8c383a70699aff92c752f2bbcc6687a726d0c2f142c9f368841b42026`. Its
source tuples are SVI version `b8b6bf61-c6a3-4538-b0df-1b88c61720b1`, run
`331f367d-9282-4936-9b49-a480db995016`, artifact
`cdc_atsdr_svi_2022_county:dc042342a2e5abc108af67b439ec04c8` (SHA-256
`dc042342a2e5abc108af67b439ec04c86da8b61f9bdd1cf1b1e5ee0462dea7b2`), and
RUCC version `87872b36-93ab-4a34-b70e-29569192cb48`, run
`f65bd68a-27de-4ad4-81a5-424ee24af3b6`, artifact
`usda_ers_rucc_2023:ec455ee2a8bc5fc8e070575ea5bee7dc` (SHA-256
`ec455ee2a8bc5fc8e070575ea5bee7dce46fc6037f8c3449cbf56e8b45331fa7`). The
source/record reconciliation reports exact manifest, completed-run, artifact,
and historical conformed-record correspondence for the 15,720 selected
semantic observation links. The later two-role PROD audit sampled eight
observations across all five measures, matched them to four historical
conformed records, and passed `validate_lineages()` (#193). This representative
PASS is distinct from the full-row aggregate reconciliation; it does not claim
that every physical observation has a persisted #190/#193 envelope. Earlier
access-blocked / DEFER receipts are superseded by the final audit and closure
receipt. The [DATA #200 closure receipt](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/issues/200#issuecomment-5987477044)
confirms PR #600 merged and prior BLOCKED/DEFER comments were superseded.
The [DATA #496 accepted handoff](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/issues/496#issuecomment-6004641971)
records #199/#200 complete and names WEB #210/#414 and API #84 as downstream
context owners. API #212 opened later and owns the specific five-measure API
implementation described below. No further source-admission or lineage gate
is proposed.

The Data handoff records historical [release build
receipt](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/actions/runs/35328355391)
and [PROD publication receipt](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/actions/runs/35362701191)
for that same release/bundle. These establish the governed release promotion,
not a public API view publication or current-head HTTP check.

Data V138 supersedes V127's original human-only allowlist. It retains those
three human measures and adds seven NLCD 2025 county measures for FIPS `09110`
and `51013`: `nlcd_forest_area_share_county_year`,
`nlcd_developed_area_share_county_year`,
`nlcd_agriculture_area_share_county_year`,
`nlcd_wetland_area_share_county_year`,
`nlcd_open_water_area_share_county_year`,
`nlcd_mean_impervious_fraction_county_year`, and
`nlcd_changed_area_share_county_year`. The documented Data #594 production
receipt reports 14 rows across those two counties, with the baseline unchanged
([receipt and owner evidence](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/issues/594#issuecomment-6106427960)).
This is bounded NLCD evidence; it does not imply that SVI or RUCC are in the
current public view. V127 remains the original projection contract, not the
current allowlist.

The October 4 reviewed metadata handoff records the five #191 envelopes as
`INTERNAL` in that storage-neutral packet. That is a scoped, dated metadata
classification, not a global public-disclosure prohibition. The Data #194
consumer projection still requires applicable reviewed consumer metadata and
lineage. DATA #199/#200 acceptance and the source/release lineage gates are
complete; DATA #496 records Matthew's approved general PROD→API product
direction. Do not reopen source admission, scientific meaning, or general
publication intent on this basis. No newer measure-specific consumer/public
metadata packet was found in the cited evidence.

### Smallest bounded implementation proposal (API #212)

This is the already-approved, narrowly bounded measure set for the API #212
implementation; it adds no source admission:

| Measure | Existing source identity | Proposed time representation |
| --- | --- | --- |
| `population_2022` | `cdc_atsdr_svi`; dataset `atsdr-svi-2022-county-layer`; `E_TOTPOP`; SVI 2022 | `PERIOD` 2018-01-01 through 2022-12-31 |
| `svi_percentile_2022` | `cdc_atsdr_svi`; dataset `atsdr-svi-2022-county-layer`; `RPL_THEMES`; SVI 2022 | Same `PERIOD` |
| `uninsured_percent_2022` | `cdc_atsdr_svi`; dataset `atsdr-svi-2022-county-layer`; `EP_UNINSUR`; SVI 2022 | Same `PERIOD` |
| `uninsured_percentile_2022` | `cdc_atsdr_svi`; dataset `atsdr-svi-2022-county-layer`; `EPL_UNINSUR`; SVI 2022 | Same `PERIOD` |
| `rucc_2023` | `usda_ers`; dataset `rural-urban-continuum-codes-2023`; `RUCC_2023`; RUCC 2023 | `VINTAGE_YEAR=2023`, never an annual date interval |

The missing deliverable is the bounded consumer projection and API contract,
not a new source-admission decision. DATA #496's October 5 handoff names WEB
#414/#210 for context consumption and API #84 for the reusable API context
contract. API #212 was opened October 8 and subsequently assigned the concrete
five-measure rurality/socioeconomic API implementation; DATA #199/#200 and the
general PROD→API product direction are already accepted. API #212 requires the
reusable context contract from
[API #84](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/84)
and is currently marked blocked until #84 is merged, deployed, and accepted.
Do not duplicate #212 or bypass that dependency. The bounded implementation
path is to project existing canonical rows with source/version/run/artifact
and record provenance, county FIPS, values/value states, and exact temporal
semantics: SVI's 2018–2022 ACS period and RUCC `VINTAGE_YEAR=2023`. API #212
sets acceptance at two representative FIPS per available measure, preserving
RUCC's category codes and SVI's observed/zero/missing states with provenance;
it requires explicit unavailable status where coverage is not proved. This
needs no new ingestion or scientific/source admission. The specific outstanding
work is the #194/#212 measure-specific consumer metadata and additive
period/vintage-aware projection. The October `INTERNAL` packet makes that
contract reconciliation necessary; general PROD/API publication intent is
already approved and is not being requested again. Until projection
publication and API #212's actual-reader acceptance, retain current 404
behavior and do not read profile values as observations.

## API contract and regression boundary

For these SVI/RUCC requests, the existing observation adapter reads
`PRESENTATION.CURRENT_COUNTY_OBSERVATIONS_V`; it has no county-profile
fallback. The API also has separate environmental and CDC adapter paths.
Current V138 retains `human_status`, `case_count_floor_2023`, and
`incidence_floor_2023` with annual 2023 periods, and adds the seven explicitly
bounded NLCD 2025 measures described above for FIPS `09110` and `51013`. SVI
and RUCC remain absent. The API measure catalog may describe known measure
metadata; catalog membership does not assert public observation availability.
A 404 from the observation endpoint is therefore the expected result when
the requested measure has no row in the current public observation view.

The public V1 observation model currently requires a start/end period. It can
represent the SVI survey window with the exact dates once a governed public
projection exists. RUCC needs an additive, explicit vintage-year semantic; a
calendar-year date pair would be false precision. Any public schema/view
extension needs OpenAPI, compatibility, and Web client review. No adapter SQL,
source admission, or public contract change is made in this evidence update.

## Remaining production gate

The original task requested a live production verification. This environment
could not reach the public API through its network proxy, so no fresh catalog,
source view, production reader, or HTTP response was queried. Current GitHub
issue and PR records were checked with the authorized connector. Existing
retained evidence establishes approved source tuples and historical semantic
rows, but not the live API's exact current release response or the completion
of API #212's measure-specific reader/contract acceptance. Do not claim
production UAT from fixtures or manifests.
