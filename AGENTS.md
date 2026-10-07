# Atlas API instructions

This FastAPI service owns the server-side REST boundary for Atlas reads,
governed Snowflake queries, and bounded knowledge-graph evidence retrieval.
The public HTTP schema is `openapi.json`; `first-party-openapi.json` includes
internal operations for existing first-party consumers. Do not expose internal
AI/RAG operations through the public projection.

## Fresh Linux checkout

Run `bash scripts/bootstrap-agent.sh` from the repository root. It installs a
local `uv` tool if necessary, selects the pinned Python 3.12 interpreter from
`.python-version`, and runs `uv sync --extra dev --locked`. Git, Python 3 with
`venv`, and access to PyPI, the Python runtime download, and the two pinned
GitHub source dependencies are needed. No `.env` or production credential is
needed for the ordinary quality suite. The script prints the `uv` path to use
if it installed a local copy.

Canonical commands (use the script's local `uv` path if `uv` is not on PATH):

```sh
uv sync --extra dev --locked
uv run ruff check .
uv run mypy
uv run pytest -q
uv run python scripts/export_openapi.py
uv run python scripts/openapi_quality.py --base-ref origin/main
uv run uvicorn lyme_gap_atlas_api.app:app --reload
```

The server can start without credentials for local smoke or documentation
work; data-backed routes require the approved service connections. The normal
pytest suite uses fakes and local fixtures. Do not place real Snowflake,
Neo4j, Supabase, OpenAI, or DigitalOcean credentials in a cloud coding
environment. `docker build .`, the production Typst regression, and the
gitleaks container are additional CI-equivalent checks when Docker is
available; Docker is not needed for the Python quality commands. The report
renderer uses a test stand-in by default; real Typst is supplied by the image.
`uv build` builds the source distribution and wheel, including report templates.
The Docker image build remains the production build gate. Repository-wide
`ruff format --check .` is not a current CI gate and reports existing formatting
drift; format changed Python files without reformatting unrelated code.

## Delivery rules

- Start from current `main` in a feature branch, usually `codex/<short-topic>`.
  Open a PR; do not push directly to protected `main`. Record scope, affected
  contracts/ADRs, security and privacy impact, test evidence, and any remaining
  limitations. One approving review, resolved conversations, and the current
  `quality` check are required for merge.
- Keep database and Supabase migrations versioned and environment-specific.
  Never run a remote migration, PROD read, or deployment from an agent setup
  job. Follow `supabase/README.md` and the workspace least-privilege rules for
  explicitly authorized integration work.
- Preserve structured, redacted observability. Follow the telemetry contracts
  under `docs/`; never log credentials, bearer tokens, prompts, patient data,
  or raw private records. Codex Cloud must not receive PHI.
- Do not change the public contract, access model, data classification,
  deployment topology, or public-health interpretation without the required
  governed decision and owner review. Do not change CI, GitHub settings,
  production configuration, or external resources as setup work.
- Before handoff, pass the applicable commands above and the focused tests for
  the change. Update and validate both OpenAPI artifacts for route or schema
  changes, and include downstream generated-client compatibility evidence.

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
- `openapi.json` and `/openapi.json` are the canonical public contract; Swagger,
  ReDoc and external documentation consume only the approved public projection.
  `first-party-openapi.json` is a complete build artifact for existing Web
  client/validator generation, never an HTTP endpoint or external-doc source.
  Preserve internal operations and schemas in that artifact. Both derive from
  the same route definitions via `scripts/export_openapi.py`; keep both drift checks.
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
uv run python scripts/openapi_quality.py --base-ref origin/main
docker build .
```
