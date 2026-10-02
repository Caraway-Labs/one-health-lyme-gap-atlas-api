# Ask Atlas service telemetry (#157)

`knowledge_chat.service` emits exactly once per `KnowledgeChatService.chat`
invocation, including dependency failures, intentional abstention and exceptions.
Its duration includes request-scoped Snowflake teardown, excludes HTTP routing,
validation before service entry, serialization, client transport and edge time.
Stage spans now become current while active: generation JSON parsing and
Snowflake details are nested beneath their owning stage, never additive totals.
Exception recording is disabled so exception text/stack payloads are not added.

The service attaches only the following `atlas.ask_atlas.*` completion attributes:

| Attribute | Contract |
| --- | --- |
| `outcome` | Closed service outcomes in `_ASK_OUTCOMES`; unknown becomes `unhandled_error` |
| `outcome_class` | `answered`, `intentional_abstention`, `failure` |
| `evidence_state` | Closed response evidence states or `unavailable` |
| `validation_outcome` | Existing closed grounding rules, `passed`, `not_run`, `invalid_generated_shape` |
| `generation_attempts` | Integer 0–2; actual answerer generation entries after the deadline guard, never rejected/blocked attempt slots |
| `retrieval_passage_count`, `retrieval_paper_count` | Integer 0–100, capped; 0 alone does not prove retrieval ran |
| `provider` | Constant `openai` |
| `configuration_version`, `retrieval_version` | Source-controlled constants |

No model identifier is added: a character/length check on arbitrary configured
text does not prove low cardinality or privacy. No request text, passages,
support quotes, output, user/network identity, capabilities, secrets, provider
payloads or URLs are copied. Existing `request.id` remains correlation only.
Intentional outcomes `no_evidence`, `safety_refusal`, `capacity_limited` remain
separate from failures. An earlier validation rejection followed by a successful
retry has final validation `passed`; inspect attempt-stage outcomes for history.

Exporter/start/attribute/end failures remain optional and cannot change answers.
No collector, sampling, retention, backend or public API contract changes occur.
The existing tail sampler can bias rate/ratio/quantile comparisons: the dashboard
shows stored telemetry, not population throughput or an unbiased failure ratio.

Dashboard ownership is the root Atlas repository, UID `ohla-ask-atlas`. Prior
traces cannot acquire these new dimensions retroactively. Deploy via the existing
API quality-gated main workflow after independent review; rollback by reverting
this telemetry change through the same workflow. API #133 remains independent.
