# Research Assistant literature response contract v1

Owner: API repository. Applies to `POST /v1/knowledge-graph/chat` for the early-access literature capability. Product decisions: KG #3 and API #14. Retrieval and operational controls: KG ADR 0007-0009.

## Boundary

The existing service retrieves only through its fixed Neo4j template over the governed, steward-approved PubMed/PMC Open Access full-text corpus. It does not search the web or abstracts, execute model-authored Cypher, or use model background knowledge when graph evidence is absent. The response `configuration_version` identifies the KG retrieval configuration; `assistant_policy_version` identifies the deployment-controlled behavior policy; `model_id` identifies the answer model when generation ran. Every answered claim must link to returned passage IDs and exact PMID matches, with a verbatim support quote from each cited excerpt. Obvious unsupported additions fail closed. Automated lexical checks are conservative safeguards, not proof of scientific entailment; peer review and pre-general-go-live evaluation remain necessary.

The first and corrective generation attempts share a quote-first claim contract: select returned passages and exact support quotes, then write short, atomic `claims[].text` with terminology and scope close to those quotes. The server builds the user-visible answer from validated claim text; the generated `answer` field must be consistent with those claims and cannot introduce broader findings. A corrective attempt adds only the fact that the previous candidate failed grounding. The deterministic validation threshold and citation requirements are unchanged.

For generation, each retrieved passage supplies its ID, exact excerpt, PMID, and paper title. Extraction summaries and PubMed URLs are excluded from the model input to reduce duplicate context; the server retains the full retrieved records for literal quote validation and citation construction. All 20 retrieved passages remain eligible, and both generation attempts use the same fields. Request logs record only the model-input character count and passage count, not the text.

Generation instructions limit a candidate to three short claims and two returned passages per claim. They require exact copying of passage IDs and PMIDs, require both sides to remain visible when cited findings conflict, and allow a remaining claim for a directly supported material limitation. This bounds candidate length after a live six-claim response cited an unknown passage ID and exhausted the corrective-attempt window. The server's validation remains unchanged and still rejects unsupported IDs, PMIDs, quotes, or claims. Numeric provider token usage is logged when returned; prompts and model output are not logged.

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

## Latency and failure behavior

The literature path has a source-controlled 28-second request deadline (`KG_CHAT_DEADLINE_SECONDS`, bounded to 10–28 seconds) and a 16-second maximum per generation call (`KG_GENERATION_TIMEOUT_SECONDS`, bounded to 3–16 seconds). At least five seconds of the remaining request budget is reserved after generation for grounding, provenance, persistence, and response serialization. These defaults use the production QA observation that embedding, retrieval, and budget reservation took about seven seconds before the first generation hit its 10-second transport timeout; the prior public edge failure appeared after roughly 40 seconds. The answer-specific OpenAI client makes no automatic transport retries. Transport and provider failures return the existing typed HTTP 503 `evidence_unavailable` response with `Retry-After: 30`. A deterministic grounding rejection may receive one corrective generation attempt only while enough budget remains; repeated rejection returns the same typed unavailable response. The API never substitutes general model knowledge for missing or invalid evidence.

This deadline is based on the observed production failure in API #107, where the public edge returned 504 before the API's 39–45-second typed 503 completed; an App Platform-specific edge timeout was not established by documentation. Request-scoped logs record stage durations and bounded outcome/error categories without prompts, passages, model output, or capability tokens. The measured stages are safety classification, embedding, fixed Neo4j retrieval, Snowflake budget reservation, each answer/grounding attempt, provenance enrichment, conversation persistence, and total duration. The chat request executes the fixed retrieval query through its long-lived driver without a separate `verify_connectivity` preflight; a failed query still returns typed evidence-unavailable. The startup graph probe remains independent of request serving. The deadline is checked before generation and before a corrective attempt; blocking provider calls receive an explicit timeout. Production timing telemetry must be reviewed before changing retrieval breadth, provenance, or Snowflake persistence behavior.
