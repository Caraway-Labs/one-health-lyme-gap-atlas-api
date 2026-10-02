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
- `openapi.json` is the complete first-party REST contract; preserve internal
  product schemas for existing Web client and validator generation. The separate
  `public-openapi.json` is the external developer projection, served at
  `/public/openapi.json`; only approved public GETs and reachable schemas belong
  there. `scripts/export_openapi.py` regenerates both; keep both drift checks.
  For endpoint, payload, error,
  pagination, cache/freshness, or provenance changes, update it with
  `scripts/export_openapi.py`, add API/contract tests, and coordinate generated
  client changes with the web repository. Breaking public-contract changes need
  an approved ADR, migration plan, and deprecation path.
- Public API / developer experience is part of done: public endpoint work is
  incomplete while either OpenAPI export, developer-facing metadata, or relevant
  contract tests are stale. Preserve intentional, unique, stable `operationId`
  values (including existing IDs), summaries, descriptions, domain tags,
  request/response examples, errors, and useful schema field descriptions.
  Document applicable pagination, query bounds, freshness/cache, provenance,
  missingness/value states, versioning, and RFC 9457 Problem Details semantics.
  Check downstream Fumadocs/OpenAPI consumers and generated-client/SDK
  compatibility; this does not authorize implementing deferred SDKs or auth.
  See `docs/public-api-v1-contract.md` and `docs/public-api-guide.md`.
- The external developer surface is governed structured data/Snowflake
  analytics, metadata/provenance, and separately approved ML outputs. Keep
  anonymous public reads. Internal Ask Atlas, PubMed RAG, Research Assistant,
  KG retrieval, and LLM orchestration routes must stay outside public OpenAPI
  unless an explicit product decision changes that boundary. Preserve their
  internal product behavior; add tests/CI to catch accidental public exposure
  and contract drift. Use the breaking-change rules above for public changes.
- Preserve source, retrieval time, geography, methodology/version, limitations,
  and `last_updated` semantics in atlas responses. For graph chat, fail closed
  without validated evidence and do not expose arbitrary Cypher.
- A push to `main` that passes `quality` deploys production. Manual
  `workflow_dispatch` with `deploy_production` is the redeploy path. See
  `docs/ci-and-production-deploy.md`. Pull-request `quality` runs do not deploy.

Run the CI-equivalent checks before handoff:

```powershell
uv sync --extra dev --locked
uv run ruff check .
uv run mypy
uv run pytest -q
uv run python scripts/export_openapi.py
git diff --exit-code -- openapi.json
git diff --exit-code -- public-openapi.json
docker build .
```
