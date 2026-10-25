# OpenAPI quality and consumer artifacts

Write all public descriptions, summaries, and examples to the
[API writing style guide](api-writing-style.md). It defines the shared vocabulary,
the approved safety wording, and copy-ready templates.

FastAPI/Pydantic route definitions own both generated contracts. Run the existing
locked dev setup, tests and export, then the semantic gate:

```powershell
uv sync --extra dev --locked
uv run pytest -q
uv run python scripts/export_openapi.py
uv run python scripts/openapi_quality.py --base-ref origin/main
```

The gate validates both OpenAPI specifications and local references without
network retrieval, validates embedded request/response examples, and checks IDs.
The public artifact additionally requires the approved path/ID fixture, domain
tags, summaries/descriptions, documented errors and anonymous access. Existing
tests protect internal first-party operation/schema compatibility and the public
projection's fail-closed boundary.

## Documentation-quality rules (public contract)

The gate also checks the prose of the public `openapi.json` against the
[API writing style guide](api-writing-style.md). Each failure prints the rule, the
operation (or schema) and field, and a link to the style-guide section behind the rule:

```text
[summary-length] GET /v1/observations: summary is 71 characters; budget 60 (see docs/api-writing-style.md#operation-summary)
```

| Rule | Fails when |
| --- | --- |
| `parameter-description` | A public parameter has no description. |
| `schema-description` | A component schema, or one of its properties, has no description. A property that is a bare `$ref` passes when the target schema is described. |
| `response-200-example` | An operation's `200` response has no example in any media type. |
| `error-description` | A non-2xx response has no description. |
| `error-example` | A non-2xx response with a body has no example. Body-less responses such as `304` need none. |
| `description-length` | An operation description is over the character budget. |
| `paragraph-length` | A prose paragraph is over the sentence budget. Tables, headings and list items are not prose. |
| `summary-length` | An operation summary is over the character budget (60). |
| `banned-term` | Public text uses a term from the style guide's "Banned and replace" list. Text in code font is exact contract values and is skipped, as are example data values. |
| `operation-id-fixture` | Operation IDs differ from `tests/fixtures/public-operation-ids.json` (public) or `first-party-operation-ids.json` (first-party). Always enforced. |
| `operation-id-naming` | An operation ID does not match the naming pattern. **Advisory only for now; see below.** |

Budgets, the banned-term list, exceptions and the operation-ID policy live in one
reviewed file, [`scripts/openapi_quality_rules.toml`](../scripts/openapi_quality_rules.toml).
Changing it changes what the public reference may say, so review it like a spec change.
Each rule is a small function in `scripts/openapi_quality.py` with a passing and a
failing fixture in `tests/test_openapi_quality.py`. The checks are offline and
deterministic, and CI runs them on every pull request in the quality job.

### Operation-ID naming is not decided yet

The stability check (`operation-id-fixture`) is enforced today: IDs may not change.
The verbNoun camelCase convention (for example `listMeasures`) is **pending the
owner's decision** on operation-ID naming (workspace issue #357). Until it is decided:

- `naming_convention` in the rules file is `"advisory"`: the gate prints one
  `advisory` line per non-matching ID and still passes.
- No operation ID has been renamed, and the generated clients depend on the current IDs.
- To enforce after the decision, rename the IDs, update both fixtures and the Web
  generator in the same reviewed change, then set `naming_convention = "enforce"`.
  Set it to `"off"` to silence the advisories.

### Adding a justified exception

Fix the text first. If a fix is impossible or would change meaning, add an entry to
`scripts/openapi_quality_rules.toml`:

```toml
[[exceptions]]
rule = "response-200-example"
location = "GET /v1/example"   # fnmatch pattern over the location the gate prints
justification = "One sentence on why a fix is impossible or wrong."
```

An entry without a justification fails the gate, and an entry that no longer matches
any finding fails too, so exceptions are removed when the cause is fixed. Do not use an
exception to raise a budget or hide a banned term; change the budget or term in the
same file, with review. There are no exceptions today.

## Changelog

[`CHANGELOG.md`](../CHANGELOG.md) is the public API changelog, linked from
`info.description`. It is kept by hand, with a template in the file, because that is the
simplest approach that needs no tooling, works offline, and lets authors write for
integrators. A pull request that changes the public spec adds its entry under
"Unreleased".

## Drift and review artifacts

Regenerated documents are compared with committed HEAD as parsed JSON. Whitespace
and object-key order are ignored; arrays, values, schemas and metadata remain
meaningful. CI exports first, then checks semantic drift. The review diff compares
canonical sorted JSON against the PR base SHA (pushes compare the preceding SHA).
It is a deterministic review aid, not an automatic breaking-change classifier.

Only after the quality job passes all checks, the workflow publishes
`atlas-openapi-<workflow SHA>` for 30 days. It contains:

- `openapi.json`: public Fumadocs/external developer contract.
- `first-party-openapi.json`: complete build input for existing Web generators;
  never publish this in public documentation or serve it as an API endpoint.
- `<filename>.diff`: semantic review diffs against the recorded base.
- `manifest.json`: exact checkout SHA, base SHA and SHA-256 for canonical files.

Consumers select a successful quality run for an exact reviewed/released commit,
verify its manifest/checksums, and use the appropriate artifact. Workflow PR SHAs
may be GitHub's tested merge ref; the manifest records that checkout identity.
After artifact retention expires, committed files remain available by immutable
Git SHA. A passing PR artifact is not evidence of production deployment.

For an intentional change, review the semantic diff, update route metadata and
the generated exports together, and coordinate downstream consumers. Stable IDs
and compatibility fixtures are guardrails, not bypass lists: change them only
alongside the explicit approved public-scope decision or breaking-change ADR,
migration and deprecation plan required by AGENTS.md and the governed V1 contract.
Keep first-party/internal capabilities out of the public allowlist. No SDK,
authentication or new public capability is authorized by these checks.
