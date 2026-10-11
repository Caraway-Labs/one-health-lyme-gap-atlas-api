# API 88 captured Data projector fixtures

The temporal JSON fixtures were generated offline from Data repository commit
`0765061e7ec16c44e4af6cec4a118169225cfa6f` with the existing synthetic
test inputs and production projector functions:

- `api88_data429_projector.json` was refreshed from Data commit
  `641620ddc65a8183d45d2c5c0522ca3a92f5a265` (includes merged PR #670).
  It uses `payload`, `case`, `reported`, and `negative` from Data's
  `test_surveillance_evidence_consumer_schema.py` and
  `test_surveillance_evidence_mapping.py`: Established, Reported, site negative,
  county tick No records, and county pathogen Present. All normalized values
  and evidence revision hashes are actual projector outputs, not API mappings.
- `api88_data430_431_projectors.json`: `compare_methodology` then
  `project_methodology_comparison`; `evaluate_alignment` then
  `project_temporal_alignment`, using the existing Data test `record()` and
  `spec()` helpers. Fractional coverage uses the same synthetic spec with
  `expected=observed=100.5` and `unit=SOURCE_SUPPORTED_AREA`.
  The committed comparison fixture retains the projector output; the test
  names its synthetic pair inputs separately.
  Additional DATA 430 projections cover `UNKNOWN`, `COMPARABLE`,
  `NOT_COMPARABLE`, and both `CAUTION_REQUIRED` paths, including a reviewed
  2021 LOW versus built-in 2022 HIGH jurisdiction-class change.

The DATA 429 envelopes have `SYNTHETIC_FIXTURE` evidence tier and no admitted
release. They are rejected unchanged by the API seam. A separately named
test helper changes copies to simulate an admitted binding solely to exercise
cross-object validation. These fixtures do not establish genuine evidence,
scientific or steward review, or production availability.
