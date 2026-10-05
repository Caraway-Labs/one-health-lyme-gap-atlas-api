# Ask Atlas typed tools v1 (API #18)

This internal, request-local service implements the `find_measures`, `get_observations`,
and `get_evidence_metadata` calls specified by
[`ask-atlas-contract-v1.md`](ask-atlas-contract-v1.md). It does not expose a new HTTP
route. `literature_answer` remains the existing Research Assistant service.

Inputs are the versioned Pydantic models in `ask_atlas_tools.py` with
`contract_version: "ask-atlas-tools-v1"`. Unknown fields, including SQL, Cypher,
repository names, physical relations, free-form source IDs, filters, sort keys,
and page tokens, are rejected. Outputs use the canonical nested public models
and the `tool_result` envelope in `ask-atlas-results-v1.schema.json`.

| Tool | Reused service | Bound |
| --- | --- | --- |
| `find_measures` | `MetadataService.discover` | one indicator or 1–120 character normalized search; at most 20 complete matches |
| `get_observations` | `MetadataService.discover`, `ObservationService.search` | one measure, 1–20 county FIPS IDs, whole annual periods, at most 200 slots/rows |
| `get_evidence_metadata` | `ProvenanceService.source` and `.methodology` | 1–20 unique IDs admitted by the preceding observation result |

Example input:

```json
{"contract_version":"ask-atlas-tools-v1","measure_id":"case_count_floor_2023","geography_type":"county","geography_ids":["01001"],"year":2023}
```

Every requested county/year slot has a `present` or `absent` coverage row.
`absent` is a bounded query finding, separate from a present observation with
`MISSING`, `ZERO`, `SUPPRESSED`, or `UNAVAILABLE` value state. The SHA-256-derived
`coverage_id` is deterministic for release, measure, county, and full-year period.
The output retains canonical evidence references and public observation/source/
methodology provenance. Source publication, Atlas acquisition/processing, and
release semantics are never synthesized.

The adapter pins the current release per request and checks it again before
returning each result. A release race or unresolved provenance discards the
result. Tool calls emit a privacy-safe `atlas.ask_atlas.tool` span with tool ID,
contract version, bounded outcome, error code, and result count. No query,
prompt, FIPS, observation, or citation text is recorded on that span.

The service returns `SOURCE_UNAVAILABLE` when a 3/8/3-second tool deadline or
12-second cumulative deadline expires. The request-local adapter then expires
and cannot admit a later result. Underlying synchronous repository I/O retains
its own configured timeout and may finish after the tool has returned; it is
read-only and its result is discarded.
