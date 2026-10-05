# Ask Atlas evaluation baseline (API #20)

`tests/fixtures/ask_atlas_eval_v1.json` is the immutable launch-depth dataset.
Changing an expected result requires a new dataset version and review. The
`ask_atlas_evals` evaluates typed outputs from the #100 `MixedAssistant` entry
point, including its actual source routing and #19 Structured assistant. Tests
use the #18 controlled tool adapter and a governed-literature response fixture.
No provider is called.

Run `uv run pytest -q tests/test_ask_atlas_evals.py`. Both named candidates,
`bounded-v1` and `observation-outage-v1`, run the same cases. The second
configuration injects a controlled observation dependency failure through the
actual tool port. It exposes the regression, so the promotion gate stays closed.
The comparison demonstrates regression detection, **not** model superiority.
`compare` requires a complete result set and blocks promotion on any failure.

The hard gates cover tool selection, value state and numeric zero, release
provenance, admitted and claim-level evidence references, tool source identity,
freshness identity/state, source routing, independent expected claim text,
literature evidence state, PMID, passage and citation identity, abstention,
unsupported requests, and conservative
`insufficient_to_compare` behavior. The existing response validators provide
additional claim/citation and provenance enforcement; the evaluator revalidates
both branch bundles before scoring to catch post-construction mutations. An
LLM judge cannot override hard failures. Subjective answer utility requires
bounded human review and is not claimed by these fixture checks.

Each result records the dataset version, code commit supplied by the runner,
provider/model, prompt/config/tool versions, structured release, literature
corpus/index/retrieval versions, case ID, and active OpenTelemetry trace ID.
The experiment runner keeps the service execution and evaluator in one trace;
the test checks the actual service span against the result trace ID.
The caller must supply actual run versions; the test candidates use explicit
fixture labels. Results contain no question, evidence body, passage, prompt,
credential, or unrestricted output. An external Phoenix exporter can consume
the existing OTel provider; this module creates no second telemetry stack.
Exporter and evaluation-backend failures must stay outside the request path.

`Measurements` holds elapsed and dependency latency, token counts, and provider
cost only when actually observed. `None` means **unmeasured**, never zero. This
fixture run provides no live latency, token, model-cost, provider-quality, or
Phoenix-export validation. The #21 operating story owns runtime budgets and
controls. Automatic aligned/partially aligned/discordant comparison is tracked
in #191; this baseline gates the current conservative Both behavior.
