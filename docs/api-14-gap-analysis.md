# API #14 current-main gap analysis

Baseline: `origin/main` at `012078f04c6f54eda35863ebcca3e3b3562fd2ef` (2026-09-27). Compared with API #14, KG #1/#3, ADR 0007-0009, the API models, OpenAPI, and tests before editing.

| Requirement | Current-main finding | Classification |
| --- | --- | --- |
| Approved full-text corpus and bounded retrieval | Existing fixed Neo4j query, governed corpus publication upstream, no web/abstract fallback or arbitrary Cypher | Already implemented; upstream admission remains a data/KG contract |
| Evidence-only response and PMID/passage links | Existing validator checks returned passage IDs and matching PMIDs, retries once, and fails closed; claim text can still say something absent from the passage | Implemented, needs refinement |
| Neo4j outage and empty corpus | `evidence_unavailable` and `no_evidence` exist; no model call in either path | Already implemented; state semantics need typed fields |
| Safety | Narrow personalized-medical and prompt-injection detection exists; wording/policy not source controlled | Implemented, needs refinement |
| Study context, conflict, answer style | Prompt mentions conflict but does not require material context, distinguish comparison states, or bound style | Implemented, needs refinement |
| Evidence state and source identity | No typed response fields | Missing |
| Rich citation and provenance | PMID, source-paper URL, passage IDs, optional PMCID/corpus unit/section/version/hashes exist | Already implemented |
| Versioned assistant behavior policy | No application policy file | Missing |
| Browser-local follow-up | Request accepts bounded `history`, but service ignores it. Optional 30-day Snowflake persistence and capability tokens follow ADR 0007 | Implemented, needs refinement; removal of operational persistence deferred pending governance decision |
| Structured data, mixed source orchestration, eval platform, UI | Owned by API #11/#100, KG #10/API #15, and Web #43/#271 | Intentionally deferred |

Scope: compatible extension to the existing `/v1/knowledge-graph/chat` response and `KnowledgeChatService`; policy and deterministic tests. The API OpenAPI contract and ADR 0007 are affected. This change retains ADR 0007's operational persistence and procedure-only Snowflake permissions while enabling browser-local history without a server lookup.
