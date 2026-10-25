# API changelog

This file lists every release that changes the public API reference (`/openapi.json`,
`/docs`, and `/redoc`). Newest release first. Each entry says whether it is
`additive`, `deprecation`, or `breaking`.

Atlas keeps this file by hand. Each pull request that changes the public reference
adds its entry under "Unreleased" and the maintainer moves it under a dated heading
when the release ships. A hand-kept file needs no extra tooling, runs offline, and
lets an author write for integrators rather than copy a pull request title. The
OpenAPI quality gate already prints the exact spec diff for review. Automatic
breaking-change classification is out of scope.

## Entry template

```markdown
## YYYY-MM-DD

### Additive | Deprecation | Breaking
- **Area**: What changed and what an integrator should do, in one or two sentences.
```

## Unreleased

No changes yet.

## 2026-10-25

Documentation and metadata release. Every change is `additive`. No route, status code,
response shape, authentication rule, or data value changed. Generated clients keep
working unchanged.

### Additive
- **Landing page**: `info.description` now explains what Atlas is, how to start, how to
  page, how errors work, and how to read results. It restates the interpretation limits
  once: results are not a diagnosis and not an estimate of an individual's risk.
- **Tags and tag groups**: Operations are grouped under task-focused tags with
  descriptions, and shown in groups in the served reference. Summaries are shorter and
  start with a verb.
- **Parameters**: Every parameter has a description that states the accepted form,
  bounds, and defaults.
- **Schemas**: Every response schema and field has a description, including how to
  read `null` and each `value_state`.
- **Operation descriptions**: Long descriptions are split into headed parts and tables,
  with the key parameters and common problems for each operation.
- **Examples**: Every operation has a real response example for its `200` response.
- **Error guidance**: Each error response says why it happens and what to change, and
  has an example of the Problem Details body for its `code`.
- **Response headers**: Each `200` response lists its headers, including
  `Cache-Control` and `X-Request-ID`, and `ETag` where the operation supports it. The
  reference explains rate limits and how to retry a `429` or `503` with `Retry-After`.
- **Code samples**: Every operation includes ready-to-run curl, Python, TypeScript, and
  R samples (`x-codeSamples`).
- **Quality checks**: A reviewed rule set now fails a pull request that adds an
  undescribed parameter or schema, a missing example, an over-long description, or a
  banned term. This protects the changes above.
