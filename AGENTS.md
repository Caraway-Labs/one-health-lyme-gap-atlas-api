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
  limitations. Pull requests remain required. Merge requires a green current
  `quality` check, resolved conversations, and an up-to-date branch. The
  GitHub approving-review count on `main` is 0 because the human author
  identity cannot approve its own Codex Cloud pull requests. Independent
  OpenAI `.agent` coordinator review is the procedural peer-review gate
  (advisory `APPROVE`, `REQUEST CHANGES`, or `BLOCKED`), distinct from a
  GitHub-native approval. The human retains explicit merge authority. Never
  auto-merge.
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

## OpenAI `.agent` peer review

Desired flow: issue or spec → Codex Cloud implements → same-repo pull
request → GitHub Actions `quality` and independent `.agent` review → repair
loop when needed → human merge → existing `main` deployment. Deployment
behavior stays in `docs/ci-and-production-deploy.md`.

The reviewer must be the OpenAI `.agent` coordinator, independent of the
authoring Codex Cloud session. That coordinator reviews Web Cursor pull
requests and API Codex Cloud pull requests with repository-specific rubrics.
The authoring session does not review its own pull request. The reviewer
does not merge and does not deploy.

On each open or update, review the actual diff, the linked issue's acceptance
criteria, architecture, contracts, security, data provenance, and PHI
boundaries, tests, CI results, and deployment side effects. Publish a GitHub
pull-request review or comment with an explicit advisory verdict of
`APPROVE`, `REQUEST CHANGES`, or `BLOCKED`, plus evidence and actionable
findings. State that the verdict is procedural. Do not represent it as a
GitHub-recognized approving review from an independent identity.

`REQUEST CHANGES` goes back to the originating Codex Cloud session. That
session updates the branch, `quality` reruns, and `.agent` re-reviews the
latest commit SHA. `BLOCKED` waits for an owner decision before merge.
`APPROVE`, together with passing current `quality`, resolved conversations,
and a branch that is up to date with `main`, is the signal to notify the
human for an explicit merge. Never auto-merge.

GitHub does not enforce the `.agent` verdict. It stays a procedural gate
until a future status-check story. Phase 1 adds no OpenAI billing or
credentials, third-party GitHub Apps, webhooks, runners, or new Actions
jobs. The recorded `main` protection change, the dry-run checklist, and
that follow-up are in
[Pull request governance](docs/ci-and-production-deploy.md#pull-request-governance).

## Governance reference resolution

These instructions support both the assembled local Atlas workspace and a
standalone API checkout. Resolve paths relative to this repository root; use
the same relative paths on Linux and Windows, with no fixed checkout location.

1. Read this `AGENTS.md`, [README.md](README.md), and the relevant API-local
   contracts and runbooks first. The delivery rules above and API boundaries
   below are the self-contained minimum for every task. Relevant local sources
   include [public API policy](docs/public-api-v1-contract.md),
   [geography](docs/county-geography-contract.md),
   [telemetry/redaction](docs/api-telemetry-contract.md),
   [migrations](supabase/README.md), and
   [production delivery](docs/ci-and-production-deploy.md).
2. In an assembled workspace, read and honor the parent `AGENTS.md` and
   `TECHNOLOGY_AND_GOVERNANCE.md` whenever present, and read the applicable
   workspace/sibling ADRs below before material work in their domain. These
   documents supplement API-local instructions; they are not prerequisites for
   bootstrapping an isolated checkout.
3. In a standalone checkout, absent irrelevant supplements do not block
   ordinary scoped development or the credential-free quality suite. Report
   missing references only when relevant to the task; never claim to have read
   unavailable documents. For a materially affected domain, obtain the
   authoritative document from its canonical source below or the owner.
4. If a required governing document or recorded owner decision is unavailable
   for a contract, security boundary, PHI/data access, migration, deployment,
   or public-health semantics change, stop that governed change and request
   the authoritative document/owner decision. An API-local summary or a missing
   file never authorizes bypassing governance.

The workspace technology and governance baseline is authoritative for
cross-repository technology and engineering governance; accepted ADRs govern
their domains. API-local contracts, generated schemas, and runbooks specify
the current API implementation within those decisions. Repository instructions
may add detail but must not weaken workspace rules. If sources conflict, appear
stale, or leave authority/supersession unclear, stop the affected change and
ask the owner to resolve it; do not guess or silently choose a weaker rule.

Reference audit (local paths are conditional; canonical sources require access):

| Guidance | Local workspace path | Canonical source / applicability |
| --- | --- | --- |
| Workspace instructions | `../AGENTS.md` | [Workspace AGENTS.md](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas/blob/main/AGENTS.md); supplemental workflow whenever present |
| Technology and governance baseline | `../TECHNOLOGY_AND_GOVERNANCE.md` | [Baseline](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas/blob/main/TECHNOLOGY_AND_GOVERNANCE.md); cross-repository governed decisions |
| ADR 0002 | `../docs/adr/0002-public-api-and-snowflake-access.md` | [Public API and Snowflake access](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas/blob/main/docs/adr/0002-public-api-and-snowflake-access.md); public contract and warehouse access changes |
| ADR 0003 | `../docs/adr/0003-geospatial-delivery.md` | [Geospatial delivery](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas/blob/main/docs/adr/0003-geospatial-delivery.md); geography/geometry delivery changes |
| KG ADR 0007 | `../one-health-lyme-gap-atlas-knowledge-graph/docs/adr/0007-knowledge-graph-and-public-evidence-chat.md` | [KG and public evidence chat](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-knowledge-graph/blob/main/docs/adr/0007-knowledge-graph-and-public-evidence-chat.md); KG evidence/chat changes |
| KG ADR 0008 | `../one-health-lyme-gap-atlas-knowledge-graph/docs/adr/0008-neo4j-community-shared-runtime-debt.md` | [Neo4j shared-runtime debt](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-knowledge-graph/blob/main/docs/adr/0008-neo4j-community-shared-runtime-debt.md); KG runtime/security changes |
| KG ADR 0009 | `../one-health-lyme-gap-atlas-knowledge-graph/docs/adr/0009-explicit-deployment-promotion.md` | [Explicit deployment promotion](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-knowledge-graph/blob/main/docs/adr/0009-explicit-deployment-promotion.md); KG infrastructure/promotion changes |

This is a reference inventory, not an exhaustive list of governed decisions.
Follow additional applicable ADRs and contracts linked by the relevant sources.
It does not require cloning other repositories or migrating DATA work to Cloud.

## Minimum API governance boundaries

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
