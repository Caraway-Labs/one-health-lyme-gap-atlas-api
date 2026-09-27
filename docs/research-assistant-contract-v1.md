# Research Assistant literature response contract v1

Owner: API repository. Applies to `POST /v1/knowledge-graph/chat` for the early-access literature capability. Product decisions: KG #3 and API #14. Retrieval and operational controls: KG ADR 0007-0009.

## Boundary

The existing service retrieves only through its fixed Neo4j template over the governed, steward-approved PubMed/PMC Open Access full-text corpus. It does not search the web or abstracts, execute model-authored Cypher, or use model background knowledge when graph evidence is absent. The response `configuration_version` identifies the KG retrieval configuration; `assistant_policy_version` identifies the deployment-controlled behavior policy; `model_id` identifies the answer model when generation ran. Every answered claim must link to returned passage IDs and exact PMID matches, with a verbatim support quote from each cited excerpt. Obvious unsupported additions fail closed. Automated lexical checks are conservative safeguards, not proof of scientific entailment; peer review and pre-general-go-live evaluation remain necessary.

## Response fields

`source_used` is `literature_evidence` for this service. It is a typed capability identity, designed for later additional Atlas data or mixed-source values under API #100. It does not advertise those capabilities now.

`evidence_state` is a bounded state describing evidence **admitted and used for this response**, not the state of all scientific literature:

| Value | Meaning |
| --- | --- |
| `single_study` | The answer cites one paper; no broader agreement is implied. |
| `consistent` | The cited papers support a compatible finding; no numerical paper threshold or scientific consensus claim is implied. |
| `limited` | Cited evidence is relevant but narrow in scope, coverage, or method. |
| `mixed` | Cited findings vary without a clearly opposed conclusion. |
| `conflicting` | Cited findings include opposed conclusions; both sides must remain visible. |
| `insufficient_to_compare` | Relevant cited papers exist, but their scopes/outcomes do not support a useful comparison. |
| `no_relevant_corpus_evidence` | No relevant passage was returned from the admitted Atlas corpus; this says nothing about evidence outside Atlas. |
| `evidence_unavailable` | Retrieval, generation, grounding, or operational controls prevented an evidence answer. |
| `not_applicable` | A safety refusal or capacity limit prevented evidence assessment; it does not describe evidence strength or availability. |

The model proposes a multi-paper state from cited evidence. One cited paper is always reported as `single_study`. The service assigns `no_relevant_corpus_evidence` to `no_evidence`, `evidence_unavailable` to unavailable responses, and `not_applicable` to `safety_refusal` and `capacity_limited`. The response model rejects mismatched status/state pairs. The state is a lightweight indicator, not a quantitative certainty estimate.

Study-context fidelity is directed by the generation instructions and tested with known passages. The service does not infer geography from capitalization as a hard grounding gate; such a heuristic could mistake a scientific entity for a place. Formal product evaluation under KG #10 will assess context omissions beyond deterministic fixtures.

`citations` preserve paper links and richer provenance: PMID, PMCID when available, claim and passage IDs, corpus unit IDs, section labels, corpus rules version, artifact ID, and contribution/JATS hashes. Where ADR 0007 persistence is enabled, the saved citation JSON also carries the answer model, retrieval configuration, and assistant policy identifiers. A simple UI may render the PubMed paper link while retaining the response's richer data for inspection and follow-up.

## Policy and conversation behavior

`src/lyme_gap_atlas_api/assistant-policy-v1.json` is packaged with the application, schema validated when the chat service uses it, and changed through normal reviewed deployment. Proactive follow-up suggestions default off. The narrow hard-refusal classes remain mandatory. Question-class strictness has only `standard` and `heightened` values and changes the model's bounded decision-support instructions, never the grounding gate.

Browser-local history can be sent in the bounded `history` request field. Only prior user questions are used to frame retrieval and answer generation; prior assistant text is never admitted as evidence. A request using browser history does not need `conversation_id` or `conversation_token`, and does not return a new capability token. The API still supports existing token-paired continuation for compatibility. When `CONVERSATION_PERSISTENCE_ENABLED=true`, the existing procedure-only Snowflake path records and purges turns after 30 days under ADR 0007. That operational persistence does not have to be read to continue with browser history. Removing it or changing its retention/write privileges requires a separate review of ADR 0007 and the data procedures. Capability tokens remain body-only and are excluded from URLs and logs.
