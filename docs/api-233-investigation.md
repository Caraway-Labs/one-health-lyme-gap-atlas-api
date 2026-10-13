# API #233 observation availability investigation

Status: contract and adapter review only; production catalog, provenance, and
reader verification are blocked by unavailable network access from this
environment. No canonical source evidence was available to authorize an
adapter change. This document does not claim that the live catalog or current
production projection has been queried.

## Evidence and adapter boundary

The county detail adapter reads `CURRENT_COUNTY_ATLAS_V` and exposes summary
fields including population, SVI percentile, uninsured percentage/percentile,
and 2023 RUCC. The canonical observation repository reads only
`CURRENT_COUNTY_OBSERVATIONS_V`; its SQL binds measure, county FIPS, requested
period, and current release. The API does not join the profile view into an
observation. The observation contract documents the currently published
human-status, case-count-floor, and incidence-floor measures. These repository
contracts show that profile values alone do not prove an observation-reader
defect. The listed measures require source-native canonical records plus
period, value-state, and release/provenance evidence before a serving fix can
be justified.

## Evidence-based availability matrix

| Measure/field | Profile representation in API code | Canonical observation evidence in this checkout | Assessment / query caveat |
| --- | --- | --- | --- |
| `population_2022` / `population` | `population` on county detail | No matching source-native observation fixture or producer contract found | Profile presence alone is insufficient. Verify catalog/provenance and governed period; do not assume 2022 from the identifier. |
| `svi_percentile_2022` / `svi_percentile` | `svi_percentile` on county detail | No matching source-native observation fixture or producer contract found | Profile presence alone is insufficient. SVI 2022 display geometry does not establish an observation. |
| `uninsured_percent_2022` / `uninsured_percent` | `uninsured_percent` on county detail | No matching source-native observation fixture or producer contract found | Profile presence alone is insufficient. Preserve publisher scale, denominator, and vintage if/when governed. |
| `uninsured_percentile_2022` / `uninsured_percentile` | `uninsured_percentile` on county detail | No matching source-native observation fixture or producer contract found | Profile presence alone is insufficient. Preserve percentile universe and vintage if/when governed. |
| `rucc_2023` | `rucc_2023` on county detail | No matching source-native observation fixture or producer contract found | Profile presence alone is insufficient. Verify actual period/vintage and coding method. |
| `state_unallocated_records_2023` | County response field (semantically state-scoped per issue) | No per-county observation contract or source-native record evidence found | Do not publish as county observations; state scope needs an approved distinct contract. |
| `pacificus_status` | County response field | Existing CDC observation identity is not part of the two-measure API #88 admission | Do not widen CDC source/measure admission based on these audit rows. |

“No evidence in this checkout” is not proof that a production catalog entry or
producer row does not exist. A catalog entry alone also does not prove a row is
available for a requested county and period. At this stage no adapter defect
has been demonstrated, and no admission or query mapping is changed.

Additional retained audit summary supplied for this investigation reports that
a 300-county audit found SVI, uninsured percentage, and RUCC in all 300 county
profiles but marked them `NOT_EXPOSED` through observations in all 300. It also
reports 19,200 result rows over 927 requests, no cross-endpoint mismatches in
that sample, and separate geography-gateway and observation-publication
defects. This is owner-supplied audit evidence, not a fresh reproduction; the
raw CSV/JSONL/summary are not attached here and the linked workbook was not
independently inspected in this environment. It strengthens the profile-only
classification but does not establish source-native observation rows or their
contractual periods/provenance. Population remains separately unverified in
the available checkout. The same summary reports 92/300 incidence observations
and 179/300 tick plus 86/300 pathogen evidence results, with corresponding
missing/NO_DATA states and timeouts; those are outside this scoped adapter
review. Score/completeness have distinct retired-model lineage and
unpublished-method caveats and are also out of scope.

## Regression guard

`tests/test_public_observations.py` covers the canonical read boundary: an
unknown measure is a typed 404, while admitted rows preserve observed, zero,
and missing states, requested geography, period, release, and provenance. The
measure-presence probe uses the canonical observation projection and does not
read county-profile fields. This is a repository-level regression check, not a
production observation or UAT result.

## Remaining gates

Complete the requested measure-by-measure validation against the current
production measure catalog, canonical view/provenance, exact valid periods,
and the least-privilege production reader. Compare profile and observation
releases without treating profile values as proof of publication. Record
representative positive, null, unsupported, wrong-vintage, and valid-vintage
responses with sanitized provenance. If canonical rows exist and the current
reader misses them, add a bounded adapter fix and regression fixture that
preserves valid zero, missing state, geography, and release lineage. If the
producer lacks those rows, keep them unavailable and resolve discovery
metadata semantics through the governed data owner. Production credentials,
service role grants, source admission, and production mutation were not used.
