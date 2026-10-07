# Tier 1 model release history

This page records shipped API-facing Tier 1 releases and material consumer
changes. It does not certify that a particular batch is published in production.

## 2026-10-06 — persisted Tier 1 county review contract (API #10, PR #198)

The API shipped `GET /v1/counties/{fips}/tier1-surveillance-priority`, reading
the current persisted county result from the governed presentation view. The
response carries model, inference batch, tier-policy, source release and commit
identities, plus its UTC generation time, reasons, and limitation reference.
The version strings in a response identify the actual persisted result; this
history entry does not designate a single model or batch as universally current.

`priority_tier` is HIGH, MEDIUM, or LOW **surveillance review priority**.
`priority_percentile` is relative to scored counties in the same inference
batch and population. `raw_model_score` is a model-native anomaly score.
`evidence_sufficiency` describes input-data sufficiency, not model confidence.
None of these fields estimates disease risk, incidence, probability, diagnosis,
or clinical risk. Percentiles across batches, model versions, populations, or
tier policies cannot be compared without separate validation.

For `NOT_ESTIMABLE`, tier, percentile, and raw score are null. A scored
`INSUFFICIENT` result still carries those three fields and must retain its
insufficiency label. A missing county result returns 404; an empty,
inaccessible, or malformed current projection returns 503. The 60-second
public cache TTL does not make `generated_at_utc` a source observation date.

The API release was merged; production result availability still requires the
separately governed Data projection and runtime read proof. No production
coverage or batch publication is asserted here.
