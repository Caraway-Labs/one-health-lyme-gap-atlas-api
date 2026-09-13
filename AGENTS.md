# Atlas API instructions

Read the workspace [AGENTS.md](../AGENTS.md), [technology and governance
baseline](../TECHNOLOGY_AND_GOVERNANCE.md), this repository's `README.md`, and
the relevant workspace ADRs before material work: [0002 public API and
Snowflake access](../docs/adr/0002-public-api-and-snowflake-access.md),
[0003 geospatial delivery](../docs/adr/0003-geospatial-delivery.md), and, for
knowledge-graph work, [ADR 0007](../one-health-lyme-gap-atlas-knowledge-graph/docs/adr/0007-knowledge-graph-and-public-evidence-chat.md)
through [ADR 0009](../one-health-lyme-gap-atlas-knowledge-graph/docs/adr/0009-explicit-deployment-promotion.md).

- This service is the sole browser-facing boundary for Snowflake. Keep all
  credentials and query logic server-side; use the approved least-privilege
  service identity and never log secrets, bearer tokens, or prompts.
- `openapi.json` is the committed REST contract. For endpoint, payload, error,
  pagination, cache/freshness, or provenance changes, update it with
  `scripts/export_openapi.py`, add API/contract tests, and coordinate generated
  client changes with the web repository. Breaking public-contract changes need
  an approved ADR, migration plan, and deprecation path.
- Preserve source, retrieval time, geography, methodology/version, limitations,
  and `last_updated` semantics in atlas responses. For graph chat, fail closed
  without validated evidence and do not expose arbitrary Cypher.
- Production deployment is an explicit, owner-reviewed promotion; a green CI
  run or merge to `main` is not authorization to deploy.

Run the CI-equivalent checks before handoff:

```powershell
uv sync --extra dev --locked
uv run ruff check .
uv run mypy
uv run pytest -q
uv run python scripts/export_openapi.py
git diff --exit-code -- openapi.json
docker build .
```
