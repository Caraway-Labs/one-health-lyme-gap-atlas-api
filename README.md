# one-health-lyme-gap-atlas-api

Public, read-only FastAPI boundary between the web application and Snowflake.

```powershell
uv sync --extra dev
uv run ruff check .
uv run mypy
uv run pytest
uv run python scripts/export_openapi.py
uv run uvicorn lyme_gap_atlas_api.app:app --reload
```

The versioned contract is committed as `openapi.json`. Production uses the
least-privilege `OH_LYME_API_SVC` Snowflake service user and key-pair
authentication. See `.env.example`; never use `SYSADMIN` in this service.
