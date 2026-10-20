# WEB #773 County Data Brief redesign — UAT package

Redesign of the default `county-v3` PDF served by
`GET /v1/counties/{fips}/report.pdf`. No Web-app PDF renderer.

## Samples (live published county payloads, Typst 0.15.1)

| County | FIPS | Role |
| --- | --- | --- |
| Modoc County, CA | 06049 | Design-reference county (Continue routine review, 19.5) |
| Worth County, IA | 19195 | Prior shipped brief reference county |
| Los Angeles County, CA | 06037 | Higher review category (Prioritize targeted follow-up, 85.6) |
| Dillingham Census Area, AK | 02070 | Outside contiguous tick scope / review unavailable |
| Lower Connecticut River Valley Planning Region, CT | 09130 | Long geography name |

`before/` = current production PDFs. `after/` = this branch. `screenshots/` = page PNGs at 2×.

## Regenerating

```powershell
# requires .ci-bin/typst.exe (pinned 0.15.1) on PATH or beside the script
uv run python scripts/render_county_brief_samples.py
```

## Checks run locally

- `uv run ruff check` on touched report modules
- `uv run mypy` on touched report modules
- `uv run pytest -q tests/test_county_data_brief.py tests/test_typst_renderer.py --require-real-typst` (14 passed)
- Manual PDF page-count, text extraction, and clickable-link checks on the five samples
