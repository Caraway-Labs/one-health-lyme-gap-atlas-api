# County socioeconomic and rurality observations (API #212)

This additive contract uses the existing anonymous `GET /v1/observations`,
operationId `listObservations`. Data V155 supplies the admitted
rows in `PRESENTATION.CURRENT_COUNTY_OBSERVATIONS_V`. No source-specific endpoint,
ingestion, ML admission, authentication or deployment topology is introduced.
ADR 0002 and Data's reviewed #199/#200 definitions/ADR 0035 remain authoritative.

Implementation evidence and deployment evidence are separate. Data V155 has
production-reader acceptance; this API increment still requires independent
review, owner merge, exact-main deployment, actual service probe and HTTP proof.

## Supported requests

| Measure | Source field | Unit and transport | vintage_year | Reference time |
|---|---|---|---|---|
| population_2022 | E_TOTPOP | people; integer population estimate | 2022 | ACS 2018-01-01 through 2022-12-31 |
| svi_percentile_2022 | RPL_THEMES | percentile 0–1; exact decimal text or integer zero | 2022 | Same ACS reference period |
| uninsured_percent_2022 | EP_UNINSUR | percent 0–100; exact decimal text or integer zero | 2022 | Same ACS reference period |
| uninsured_percentile_2022 | EPL_UNINSUR | percentile 0–1; exact decimal text or integer zero | 2022 | Same ACS reference period |
| rucc_2023 | RUCC_2023 | categorical integer code 1–9 and source category label | 2023 | VINTAGE_YEAR; no period dates |

```text
/v1/observations?measure_id=rucc_2023&geography_type=county&geography_id=01001&geography_id=08001&vintage_year=2023
/v1/observations?measure_id=svi_percentile_2022&geography_type=county&geography_id=01001&geography_id=08001&vintage_year=2022
```

Context requests require `vintage_year` and reject `year`, `start_date`, and
`end_date`, including a fictitious annual RUCC period. Wrong vintages and
vintage filters on other measures return 400 UNSUPPORTED_FILTER; missing vintage
returns 400 INVALID_REQUEST. Geography, duplicate FIPS, stratification, page-size
and configured result bounds retain existing validation. FIPS remains a string.
Each admitted context county has at most one record for the selected vintage.

The repository binds all values, the five-measure allowlist, contract marker,
vintage, release, page size plus one lookahead, and offset as SQL parameters.
The existing connection, statement cap and request-owned session pattern apply.
There is no RAW/CONFORMED/private semantic-table fallback.

## Typed response and compatibility

The existing dated `Observation` schema continues to require real dates. The operation gains a separate `CollectionEnvelope[CountyContextObservation]` alternative sharing the existing
source/provenance/value-state fields, with `county_context` required. Its dates
are null only for the vintage classification; SVI retains the full ACS period.
Integer population/category values are JSON integers; nonzero decimals use
exact numeric strings rather than silently rounded binary floating point.

`county_context` contains the Data contract version, product/classification
vintage, original source field, integer/number type, authoritative category label
(null for quantities), definition, permitted value states and metadata methodology
version. Category labels are transported from Data, never derived or ranked by
the API. RUCC increments are not distances and categories must not be averaged.

The public and first-party exports retain the published
`CollectionEnvelope_Observation_` component/codegen name, its required fields,
the original Observation schema, stable operation IDs and anonymous boundary.
The original dated collection component remains unchanged; the operation adds a
separate typed context-collection response alternative. Internal annual Ask Atlas and
report adapters continue to require the dated alternative; no context feature is
silently enabled in those consumers. Existing date-based cursors retain their
fingerprint because absent vintage filters do not alter the serialized query.

Consumers of the new measures use the generated context collection. Existing
dated collection parsers keep their original non-null dates. Existing clients requesting existing measures continue
to receive their original dated shape. Web #474 should generate from the full
first-party artifact and select only the five supported IDs/vintages.

## Provenance, errors and interpretation

`metadata_revision_id` and `semantic_version` preserve Data's reviewed revisions
(SVI 1.0.0, RUCC 2.0.0), distinct from release schema version. Source resource key,
lineage source ID, dataset/product identity, source vintage and URL, actual
observation acquisition timestamp, transformation version, release methodology,
release identity and evidence reference remain separate. Publisher publication
time stays null/unknown. Internal source-version/run/artifact/record/hash fields
and full INTERNAL metadata envelopes are not exposed. Their reproducible
admission remains bound by Data's current published release and reviewed tuples.

The uninsured percentage uses the civilian noninstitutionalized population;
E_TOTPOP is not that denominator. Percentiles are national county ranks for the
specified SVI product, not percentages of people. These are descriptive county
context, with no causal, individual-risk, diagnosis or automatic ML claim.
Preserve both structured-source limitation texts and observation/release limits.

Observed, genuine ZERO and MISSING are distinct. Reviewed SVI admits all three;
RUCC admits OBSERVED only. Unadmitted/unavailable evidence is not fabricated as
an observation. A supported request without admitted observations returns 404
RESOURCE_NOT_FOUND. A partial/multi-page response contains admitted rows only;
omitted counties do not mean zero or an observed negative finding. Query one
county to distinguish its explicit unavailable selection. Read/access/decoding,
duplicate or release-consistency failures retain privacy-safe 503 semantics.
Unsupported filters return Problem Details rather than silently changing time.

Page tokens bind filters and current release. Existing public caching/ETag,
rate-limit, telemetry redaction and refresh behavior apply. A new Data release
without reviewed context admission must not fall back across releases/vintages.

## Acceptance and Web #474 handoff

Actual service access is checked at startup using the existing API key-pair
connector: primary OH_LYME_API_SVC, intended environment reader, database and
warehouse must match before two bounded presentation SELECTs. At most ten
representative observations are decoded (eleven fetched to detect excess), with
a five-second-or-lower statement cap. The redacted
`atlas_county_context_reader_probe` event reports identity/publication status and
counts/fingerprints only. It neither exposes credentials nor gates health.
Human PAT and synthetic fixture results do not satisfy this service gate.

After approved merge/deployment, verify ten anonymous observations for 01001 and
08001, three real zeros (SVI 49029; insurance 48301), deterministic unavailable/
unsupported outcomes, and existing human/climate responses. Compare to the Data
production evidence linked on #212. Record exact source/deployment SHA and probe
event before closing #212. Web #474 can then consume the generated typed values,
source labels, references and limitations with categorical RUCC display and
accessible non-map parity; no Web implementation is included here.

Rollback is a reviewed API revert through the existing deployment workflow. It
does not rewrite Data migrations, source history, or production grants.

### Generated-client verification and accepted migration

A scratch checkout of Web main `b7d90a6` regenerated React Query and Zod clients
successfully with Orval 8.24.0. Generated validators retained all ten production
context samples and validated the existing dated response example. These checks
used no edits to the actual Web repository.

The widened operation return type requires consumers to narrow the dated/context
collection before using dates or passing results to dated-only helpers. Direct
TypeScript checking identifies new errors at existing compare, explore,
investigate and PDF callers/tests. This is a generated-client source compatibility
change despite unchanged legacy HTTP payloads and schema components. Do not treat schema-floor tests as proof of generated-client source compatibility. The owner approved the additive contract and Web #474 migration on 2026-10-17; [ADR 0018](adr/0018-vintage-aware-county-context-observations.md) records the decision. Independent exact-commit re-review remains required.

Web main also has six pre-existing NO_RECORDS exhaustiveness errors and a public
documentation checksum mismatch; those are baseline findings, not API acceptance.
The API implementation is not yet deployed and actual API-service identity and
anonymous HTTP acceptance remain pending.
