# one-health-lyme-gap-atlas-api

Public FastAPI boundary between the web application and Snowflake, with fixed,
governed Neo4j evidence retrieval for the knowledge-graph feature.

The additive canonical public V1 contract is described in
[`docs/public-api-v1-contract.md`](docs/public-api-v1-contract.md). The generated
`openapi.json` is its authoritative public HTTP schema; `/openapi.json`, `/docs`,
and `/redoc` consume the approved public projection.
`scripts/export_openapi.py` also generates `first-party-openapi.json`, a complete
build artifact for existing first-party Web client and validator generation.
On a future coordinated refresh, copy that full artifact to Web's existing
`contracts/openapi.json` before running its unchanged `generate:api` command.
Do not replace that Web input with the public projection. No full-schema HTTP
endpoint is provided; current checked-in Web inputs remain untouched.
Start with the [public API guide](docs/public-api-guide.md) for tested requests,
interpretation, pagination, errors, and reproducibility.
The [OpenAPI quality gate](docs/openapi-quality.md) documents semantic review
diffs and validated public/first-party CI artifacts for downstream consumers.
The [documentation surface contract](docs/documentation-surfaces.md) defines
machine, Swagger, retained ReDoc and Fumadocs purposes and the Web handoff.
For operator signals, see the [API indicator contract](docs/api-operational-indicators.md)
and [natural-traffic validation runbook](docs/production-observability-validation.md).
Indicator and measure discovery (#53), bounded county observation queries
(#54), and source/methodology metadata (#55) read governed current-release views.
Geography retains its documented 503 until its owning story is delivered.
The [county geography contract](docs/county-geography-contract.md) distinguishes
stable FIPS identity, existing SVI display geometry, and internal TIGER analysis geometry.

```powershell
uv sync --extra dev
uv run ruff check .
uv run mypy
uv run pytest
uv run python scripts/export_openapi.py
uv run uvicorn lyme_gap_atlas_api.app:app --reload
```

## PDF renderer development

PDF exports use the pinned Typst binary in the production image. Build and verify it locally with:

```powershell
docker build -t lyme-atlas-api:pdf .
docker run --rm lyme-atlas-api:pdf typst --version
```

`TypstRenderer` accepts only server-registered template keys, not paths or client-supplied Typst.
`county-v1` and `state-v1` are immutable server-side identifiers. They consume normalized JSON
written by the report layer as `input.json`; report data is never interpolated into Typst source.
Shared components live in `reports/templates/shared/v1`. Material template/schema changes require
a new template version rather than modifying an already released v1 layout. Defaults can be overridden with
`PDF_RENDER_TIMEOUT_SECONDS=5`, `PDF_MAX_PAGES=50`, `PDF_MAX_REPORT_ITEMS=5000`,
`PDF_MAX_INDIVIDUAL_ASSET_BYTES=5242880`, `PDF_MAX_AGGREGATE_ASSET_BYTES=20971520`, and
`PDF_MAX_PDF_BYTES=26214400`. Rendered artifacts use a bounded in-process cache by default;
configure it with `PDF_CACHE_ENABLED=true`, `PDF_CACHE_TTL_SECONDS=300`, and
`PDF_CACHE_MAX_ENTRIES=128`. A cache hit returns the original artifact, including its
generation timestamp, until the TTL expires or a material report input changes.

## PDF export operations

The API exposes only versioned, server-registered templates: `county-v1` and `state-v1`.
Clients may select a supported template key but cannot supply Typst source or filesystem paths.

```powershell
curl.exe -L -OJ "http://localhost:8000/v1/counties/08001/report.pdf?template=county-v1"
curl.exe -L -OJ "http://localhost:8000/v1/states/CO/report.pdf?template=state-v1"
```

PDF responses include `ETag` and `Cache-Control`. Send `If-None-Match` to avoid downloading an
unchanged artifact. Renderer failures are returned as problem responses: `413` indicates a
configured resource limit, while `503` indicates a retryable renderer timeout or unavailable
renderer. Do not expose or add client-provided template paths, Typst markup, or asset paths.

For a future template version, add a new immutable server-side key and template directory, update
the report contract and tests, then build the production Docker image and exercise both report
routes. Templates use US Letter pages, structured headings, explicit table headers, text labels in
addition to color, and page footers. Missing metric values are rendered as "Data unavailable",
which remains distinct from a legitimate zero.

The versioned contract is committed as `openapi.json`. The repository's
`.python-version` (Python 3.12) controls `uv` and OpenAPI export, matching
the production image and hosted quality job. This keeps generated HTTP status
descriptions stable across local and CI runs.

Production uses the
least-privilege `OH_LYME_API_SVC` Snowflake service user with the
`OH_LYME_PROD_READ` role and key-pair authentication. Atlas reads use the
configured `SNOWFLAKE_PRESENTATION_DATABASE` and
`SNOWFLAKE_PRESENTATION_SCHEMA` semantic-release interface; production must set
both its connection and presentation databases to
`ONE_HEALTH_LYME_GAP_ATLAS_PROD`. See `.env.example`; never use `SYSADMIN` in
this service.
Neo4j Community runtime authorization is recorded as accepted debt in the
knowledge-graph repository's ADR 0008: API retrieval remains constrained by
the application, network, and feature-flag controls rather than database roles.

## Production promotion

App Platform autodeploy is off (`deploy_on_push: false`). A push to `main`
deploys production after `quality` succeeds. **workflow_dispatch** with
`deploy_production` redeploys that commit. Overlapping promotions serialize on
`atlas-api-production` and skip any candidate that is no longer `origin/main`.
See [CI and production deployment](docs/ci-and-production-deploy.md) and the
[public API support runbook](docs/public-api-support.md).
