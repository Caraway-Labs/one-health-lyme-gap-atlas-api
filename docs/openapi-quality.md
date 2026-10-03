# OpenAPI quality and consumer artifacts

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
