# Structured Ask Atlas orchestration (API #19)

The internal `POST /v1/assistant/structured` endpoint accepts a question,
explicit source mode, and bounded context. It returns an `ask-atlas-v1`
answer envelope plus the request-local `tool_evidence` used to form each claim.
It appears in `first-party-openapi.json` and stays outside public OpenAPI.
The existing literature endpoint and its API #14 behavior remain separate.
`Literature` and `Both` requests to this Structured endpoint return
`SOURCE_UNAVAILABLE`; mixed composition belongs to API #100.

The router recognizes only the #17 Structured question classes: measure
discovery, observation lookup, county comparison, bounded coverage gaps,
provenance, and freshness. It requires explicit governed measure and county/year
context before reading observations. Ambiguous requests ask for clarification;
the question must identify a supported measure subject and the selected county
FIPS, except for an explicit single-county context reference. Conflicting measure
subjects and county FIPS do not borrow an answer from the supplied context.
The deterministic router intentionally asks for clarification when wording cannot
be reconciled to the selected governed scope. It admits only finite, reviewed
question forms that consume the full input. Unknown subjects or qualifiers,
including disease substitutions, demographic strata, rates, clinical purposes,
and named places outside the selected FIPS, stop before dispatch. Measure
discovery also requires its search/indicator selector to match the question.
Explicit Lyme case-count forms bind to the governed `case_count_floor_2023`
measure and `cases` unit; unrelated selected measures cannot answer them.
Every stated county and annual period must equal the selected scope. New
phrasing requires a reviewed form rather than a guess from selected context.
Unsupported filters, predictions, causal or official public-health claims,
clinical advice, SQL, Cypher, and repository access cannot trigger a tool call.
No model or user-selected tool name reaches dispatch.

One request creates one #18 `StructuredTools` adapter. Dispatch permits only
`find_measures`, `get_observations`, and `get_evidence_metadata`. It never
calls a repository, Snowflake, the API's own HTTP endpoint, or the literature
retriever directly. The #18 per-tool and total deadlines apply; this layer does
not retry a failed or timed-out call. Final release verification also uses the
remaining total deadline and clears request-local admission on timeout. A returned
tool model is serialized and validated afresh to catch mutated nested fields.
The answer discards malformed, mismatched,
or release-changed evidence. Claim prose comes from fixed templates using
validated tool fields. The response validator recomputes each claim from the
cited tool result; an arbitrary sentence with a valid citation is rejected.

`ZERO` is rendered as numeric zero; `MISSING`, `SUPPRESSED`,
`UNAVAILABLE`, and `NO_COUNTY_LINKED_RECORD` cannot support numeric answers.
An absent coverage slot is cited by its exact request-local coverage ID, never
as an observation or as zero. A numeric request spanning both present and absent
slots abstains with the exact absent county/year slots in its limitations; the
complete bounded coverage remains in `tool_evidence`. Observation claims cite canonical
`EvidenceReference` objects and carry a freshness record. Source, methodology,
release, geography, period, unit, value state, and limitations remain available
in the tool evidence and in the applicable claim/answer fields.

The current repository has no governed, source-specific freshness policy.
Consequently, the endpoint reports freshness as `unknown`, and a direct
current/stale question abstains with `INSUFFICIENT_EVIDENCE`. It does not accept
a caller-provided threshold or timestamp as a policy. A future documented
policy can enable `current`/`stale` without changing the evidence boundary.
The synthetic `stale_policy` fixture exercises rejection of caller-supplied
freshness policy; classification under a future governed policy is deferred.

Privacy-safe OTel spans record source mode, categorical outcome, call count,
claim count, and the #18 tool identifiers/status. They do not record question
text, county FIPS, observations, citations, credentials, or response bodies.
