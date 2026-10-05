# Additive mixed Assistant branch bundle (API #100)

Status: draft for product and independent review. This document does not claim
four-state live comparison support or authorize rollout.

`POST /v1/assistant/mixed` is an internal first-party API operation. It is
absent from the public OpenAPI projection. Its `ask-atlas-mixed-v1` response
adds typed `structured` and `literature` branches while leaving the existing
`ask-atlas-v1` Structured flat answer and the API #14 Literature response
unchanged. It makes one bounded #19 Structured service call and at most one
existing #14 Research Assistant call, in-process. It does not issue arbitrary
SQL/Cypher or create another retrieval/index path.

## Current behavior and limits

- `source_mode` chooses Literature, Structured, or Both. With Both, a fully
  consumed reviewed Structured question routes to Structured, a Literature
  question routes to Literature, and a reviewed two-sentence form routes to
  both. Unsupported Atlas count or county wording asks for clarification
  before a literature call. Explicit Literature research questions use the
  existing governed literature safety policy, with an entry guard for
  personalized medication advice; Structured wording retains the stricter
  Structured guard. Nonpersonal published treatment and diagnostic research
  remains eligible for the literature service.
  Arbitrary database requests refuse before either branch runs.
- Each admitted branch retains its complete native claims, citation IDs,
  passage IDs, canonical observation references, release, and replay/config
  identifiers. The response's `actual_sources_used` lists only branches with
  admitted cited claims. One failed dependency does not erase the other branch.
- When both branches answer, `cross_source_state` is
  `insufficient_to_compare` and the overall outcome is
  `INSUFFICIENT_EVIDENCE`. The API does not derive agreement, partial
  agreement, discordance, causality, or an Atlas explanation from literature
  prose, paper titles, or graph polarity. Missing literature is not evidence
  of absence. A structured zero remains distinct from a missing row.
- If no branch can answer because its source is unavailable, the endpoint
  returns the typed `SOURCE_UNAVAILABLE` envelope with HTTP 503 and
  `Retry-After: 30`. When another branch has a grounded answer, that answer
  remains HTTP 200 with an explicit limitation for the failed branch. A typed
  literature retrieval failure or capacity limit and a typed Structured
  `SOURCE_UNAVAILABLE` are source failures; genuine no-evidence remains an
  evidence limitation.
- A cross-source limitation is separate from the two original branch claim
  sets. No citation ID or claim ID is rewritten to create a synthetic mixed
  claim. The current endpoint therefore does not supply a flat synthesized
  cross-source finding. The accepted #17 flat answer semantics remain in the
  existing Structured contract; this additive bundle needs review before Web
  treats it as the final unified answer shape.

## Downstream rendering

Web #425 can render each branch under its own source heading and make the
source-used indicator from `actual_sources_used`. It should show the overall
comparison limitation when both branches answer and should not render a
single aligned/discordant conclusion from these branch findings. Literature
PMID/PMCID/article and passage citations remain in `literature`; Atlas
observation, measure, coverage, freshness, and release remain in `structured`.
The current internal route should not be advertised as completing all four
mixed states or general go-live evaluation.

## Evidence gap for fuller comparison

The governed KG `SemanticEdge` contract has edge polarity, assertion basis,
optional study geography IDs, and optional free-text study period, linked by
`evidence_passage_id` and `paper_id`. The current fixed Research Assistant
retrieval result returns passage text and paper identity, not those edges;
its public answer has no typed scope dimensions. No bounded edge lookup or
validated mapping from edge meaning to a canonical Atlas measure has been
verified. A future comparator must establish geography, period, species,
population, outcome, and relationship meaning from admitted source evidence,
preserve assertion basis, and return insufficient whenever a material field
is absent or ambiguous. Polarity alone cannot establish agreement with an
Atlas observation. The approved product scope decision is pending.
