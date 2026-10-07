# Investigate county PDF context (API #199 / WEB #460)

The existing anonymous endpoint has an additive, opt-in server-owned template:

```http
GET /v1/counties/08001/report.pdf?template=county-v2&dataset_version=alpha-2026&period_start=2023-01-01&period_end=2023-12-31&measure_id=tick_survey
```

This is a **representative fixture request**, not a claim that `tick_survey` is
published in the current governed release. Actual selectors must be canonical
measure IDs already returned by `/v1/observations`. Never send source names,
source content, caveats, values, or Typst from the browser.

`county-v2` requires dataset_version, exact inclusive period_start/period_end,
and one to 20 distinct repeated measure_id parameters. Every selected measure
must return nonempty, unpaginated canonical observations for exactly that county,
release, and complete period. The interval is bounded to 500 logical day buckets
per measure, with at most 500 returned observations per measure; broader or
paginated selections fail rather than being truncated. A different date range
cannot relabel an annual observation. The request is independent of browser
navigation/history: the same selectors reproduce the same evidence meaning.

The ReportService uses the existing ObservationService/Snowflake presentation
read and canonical Observation model; it does not introduce SQL or a second PDF
pipeline. Renderer JSON preserves the full canonical observations: value/state,
period, source resource and lineage identities, dataset, provenance reference,
methodology and versions, timestamps, evidence reference and all limitations.
The new immutable Typst template displays observation source context and caveats
separately from release limitations. Empty release-wide sources never replace
observation provenance. V2 omits legacy score and fixed-year summary sections
from the rendered artifact because they are not observation-period evidence.

Missing source identity/label, lineage source, dataset, provenance reference or
required versions; empty/paginated results; malformed canonical data; and
release/geography/period mismatch fail with 503 Problem Details. Unknown measures
return 404; unknown county/release returns 404. Missing/conflicting selectors or
unsupported template return 422. Query bounds fail with 400 QUERY_TOO_BROAD.
Renderer limits remain 413, renderer timeout/failure 503. Missing URL, vintage,
method description and acquisition time are shown as unavailable, never inferred.
Null values remain Data unavailable with their original value_state; zero stays
zero. OBSERVED is the canonical value state, not a model classification. The
canonical model does not supply a separate observed-vs-modeled flag; v2 explicitly
states that modeled status is unavailable unless supplied by governed methodology.
No score, disease risk, predicted incidence, or causal interpretation is added.

V2 returns application/pdf with attachment filename, content-derived ETag, and
Cache-Control: no-store. All authoritative observations are fetched and checked
before the in-process cache or If-None-Match. The existing cache hashes every
normalized report input except generation time, including full observations,
source metadata/caveats, release, geography, period, settings and template. A
failed context read or failed render cannot fall back to a prior artifact. A hit
retains the original generation timestamp. PDF error responses are no-store.

## Compatibility and Web handoff

Default `county-v1`, explicit `county-v1`, state-v1, existing operation IDs,
score selectors and legacy attachment/cache behavior remain supported. V1 rejects
observation selectors rather than implying that it honored them. No public
Observation schema change is needed. Both OpenAPI artifacts add the three
optional HTTP selectors and document v2's conditional requirements.

After merge, WEB #460 should consume the exact merged `first-party-openapi.json`
for its existing full client/validator generation (public documentation consumes
`openapi.json`). Re-enable export only where the visible evidence is canonical
and shares one exact county, release and complete observation period, and all
visible measures are selected. Compare observation source IDs/lineage, provenance
references, periods, versions and caveats with the screen; release metadata alone
is insufficient. Mixed periods, noncanonical tick/status summary evidence,
missing provenance, unsupported measures, or unavailable data must retain honest
PDF unavailable. Do not silently substitute county-v1 after a v2 error.

The WEB460 regression fixture is Adams County 08001, alpha-2026, 2023-01-01
through 2023-12-31, Tick survey, and “Surveillance sites do not represent the whole
county.”, with release sources=[] and limitation Sample. Tests compare the
report's canonical observation JSON to the visible `/v1/observations` response,
then extract these identities and caveats from a real Typst PDF. This proves the
representative mapping, not production tick-source availability. The current
surveillance projection documents only human_status, case_count_floor_2023 and
incidence_floor_2023; no tick observation is invented or queried in production.

This draft does not update WEB #460 as though merged. Its owner can use this
contract and the PR's exact-SHA evidence for the post-merge Web handoff. The
independent OpenAI `.agent` coordinator review and human merge remain pending.

References: public-api-v1-contract.md, county-geography-contract.md,
api-telemetry-contract.md, workspace technology/governance baseline and ADR 0002.
No access model, data classification, geography, production configuration or
public-health interpretation changes are introduced.
