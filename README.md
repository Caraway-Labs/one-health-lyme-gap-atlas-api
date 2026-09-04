# one-health-lyme-gap-atlas-api

Public FastAPI boundary between the web application and Snowflake, with fixed,
governed Neo4j evidence retrieval for the knowledge-graph feature.

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
`PDF_MAX_PDF_BYTES=26214400`.

The versioned contract is committed as `openapi.json`. Production uses the
least-privilege `OH_LYME_API_SVC` Snowflake service user and key-pair
authentication. See `.env.example`; never use `SYSADMIN` in this service.
Neo4j Community runtime authorization is recorded as accepted debt in the
knowledge-graph repository's ADR 0008: API retrieval remains constrained by
the application, network, and feature-flag controls rather than database roles.
