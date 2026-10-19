# Ask Atlas generation: controlled live benchmark

Measured 2026-10-18 (America/Denver). This is live OpenAI generation on fixed
published literature excerpts, **not production retrieval or HTTP acceptance**.
The complete sanitized measurements and public-source hashes are in
[`benchmarks/ask-atlas-generation-2026-10-18.json`](benchmarks/ask-atlas-generation-2026-10-18.json).

The selected implementation keeps `gpt-5.6-luna`, changes reasoning effort from
`low` to `none`, uses strict Structured Outputs, and emits at most two atomic
claims. Each claim selects one enumerated literal sentence or adjacent sentence
pair before generating text. The server derives exact quotes, passage IDs,
PMIDs and claim IDs; it builds the answer from validated claims. An additional
model-generated answer is unnecessary. One corrective attempt receives an
allowlisted validation reason. The existing scientific grounding, citation,
evidence-state, safety, persistence and deadline checks remain active.

## Measured comparison

Two topics (tick surveillance and mammal reservoirs), two repetitions per
variant, identical evidence, one shared HTTP connection pool per run and rotating
variant order. Both topics supply 20 passages from two published papers each.
Baseline is budget-bypass commit `3e95c3091ff3559061ee9a3c09bfe40ff4cbcdd7`.

| Variant | First-call grounding | Answered after allowed retry | Median first call | Median turn, including failures |
| --- | --- | --- | --- | --- |
| Previous generation, Luna / low | 0/4 | 1/4 | 7.19 s | 13.65 s |
| New generation, Luna / low | 4/4 | 4/4 | 3.97 s | 3.98 s |
| New generation, Luna / none | 4/4 | 4/4 | 1.74 s | 1.77 s |

Median first-call output usage fell from 672 tokens to 266 (`low`) or 117.5
(`none`). Strict reference enums increased input usage: 4,266–4,559 baseline
tokens versus 5,883–6,755 candidate tokens. Output reduction and avoiding retries
outweighed that input increase in these samples.

A final four-turn confirmation after clarifying that verbatim claim text is
allowed passed grounding on the first call in **4/4**, with valid canonical
PubMed citations and no retries. Median first call was **1.90 s**; median turn
was **1.91 s**, with a **1.74–6.22 s** turn range. That 6.22-second sample is
retained; inference, network, schema preparation and provider queueing are not
separately measurable here. The source-file hash is recorded with this run.

A small alternative-model screen (two turns each, `none`) also passed first-call
grounding in 2/2 for both models: `gpt-6-luna` median first call 4.25 s;
`gpt-5.6-terra` 1.62 s. This does not establish a reliable model ranking. Retain
the current model and avoid combining a model migration with this correction.

## Method, reproduction and limits

`scripts/benchmark_knowledge_generation.py` is an opt-in paid benchmark, never
part of ordinary pytest. Supply a local JSON list of cases with `case_id`,
`question` and `evidence` dictionaries matching `Evidence`. Keep prompts,
excerpts, generated text and credentials out of published artifacts. The local
input remains in ignored `.diagnostics/benchmark-evidence.json` in the isolated
worktree; the committed artifact contains hashes, counts, public source metadata
and timings only.

With `OPENAI_API_KEY` supplied process-locally:

```powershell
uv run python scripts/benchmark_knowledge_generation.py --evidence .diagnostics/benchmark-evidence.json --output .diagnostics/comparison.json --live
uv run python scripts/benchmark_knowledge_generation.py --evidence .diagnostics/benchmark-evidence.json --output .diagnostics/selected.json --comparison selected --live
```

The evidence preparation selected the first ten body paragraphs per paper that
contain `tick|borrelia|reservoir|host` (case-insensitive), with normalized
whitespace and length 150–1,200 characters, from the four Europe PMC full-text
XML URLs in the manifest. PMID identity was checked against the XML. These are
public benchmark inputs, not an assertion of governed production admission.

Each turn has at most two provider attempts, zero SDK retries, a 16-second
per-call timeout and the existing 28-second deadline / five-second post-generation
reserve policy. At most 12 turns / 24 calls are allowed per invocation. Results
contain no model answers or unrestricted provider exceptions. Individual call
times use `perf_counter`; the initial comparison's total times used Windows
`monotonic` with approximately 16 ms resolution. Final confirmation uses
`perf_counter` for total elapsed time as well.

Earlier development probes demonstrated that integer fields alone permit invalid
references, and multiple references to one passage can select nonadjacent text.
The final scalar enum removes those ambiguities without expanding permitted
support spans. Unknown references, injected fields, unsupported wording/numbers/
causality, missing evidence and invalid evidence states still fail closed.

This is a small development benchmark, not a held-out scientific-quality study
or a p95 estimate. No Snowflake stage, embedding, Neo4j, production persistence,
HTTP status or production workload was measured. Dynamic schema preparation and
traffic variance remain possible latency costs. Oversized reference enums fail
under the existing generation-budget classification rather than truncating
evidence. After authorized deployment, verify two actual grounded responses and
their existing stage logs before declaring production success.

The implementation follows [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
and the model's supported [reasoning settings](https://developers.openai.com/api/docs/models/gpt-5.6-luna).
Schema adherence does not replace factual grounding.
