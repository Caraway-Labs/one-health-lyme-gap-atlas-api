# OpenAPI response examples

Each `<operationId>.json` file holds the named `200` examples for one public operation.
The API loads them at import time (`public_docs.load_response_examples`) and adds them to
the generated OpenAPI documents, so Redoc and Swagger show them. Other tooling, such as
generated code samples, can read the same files.

The files live inside the package, not under `tests/`, because the running service builds
`/openapi.json` and the container image does not include `tests/`.

## File shape

```json
{
  "_capture": {"captured_on": "YYYY-MM-DD", "requests": {"<example name>": "<GET path>"}},
  "application/json": {
    "<example name>": {"summary": "...", "value": {}}
  }
}
```

- Top-level keys that start with `_` are notes for maintainers. They never reach the spec.
- Every other top-level key is a response media type. `description` is optional and is used
  for CSV, GeoJSON, and PDF examples to document the response headers.
- JSON examples are trimmed captures of real production responses. The `_capture.requests`
  map records the exact request for each example.

## Capture record

| Item | Value |
| --- | --- |
| Captured on | 2026-10-24 |
| Release (`release_id`) | `governed-2026-10-15-january-climate-prod` |
| Source | Anonymous `GET` requests to `https://api.onehealthatlas.org` |

The individual Tier 1 priority resource carries its own `release_id`
(`governed-2026-09-18-unknown-coverage`), which is part of the real response.

## Trimming rules

- Arrays keep their first one or two items (the CSV keeps its header row and two data rows,
  and the GeoJSON collection keeps one feature). All keys are kept.
- `meta` and `links` are kept whole.
- PDF examples show only the first bytes of the file. The file's headers are described in
  `description`.
- Examples use the documented sample counties `36061` and `08001`. Nothing is edited by hand.
- Do not add credentials, tokens, or personal data. A test scans these files for markers.

## What is not here

- Non-`2xx` examples. Error responses are illustrative and live in `public_docs.py`.
- A `SUPPRESSED` observation. No `SUPPRESSED` value exists in the current production release
  (the counts for the 2023 case-count floor were `OBSERVED` and `MISSING` only), and examples
  are never invented. Add one with the next capture that returns it.

## Refresh the examples

Run this deliberately, for example after a new release is published. It sends read-only
requests with no credentials, pauses between them to respect the public rate limit, and
fetches each PDF route once.

```bash
uv run python scripts/capture_openapi_examples.py
uv run python scripts/export_openapi.py
uv run pytest tests/test_public_openapi_metadata.py tests/test_typed_availability.py
uv run python scripts/openapi_quality.py --base-ref origin/main
```

Update the capture record above, review the diff, and commit the fixtures together with
`openapi.json` and `first-party-openapi.json`.
