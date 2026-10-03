# Atlas documentation surface contract

| Stable URL | Purpose and audience | Source |
| --- | --- | --- |
| `https://api.carawaylabs.com/openapi.json` | Canonical public machine-readable HTTP contract; generators, CI and reference integrations | Public projection of native FastAPI/Pydantic definitions |
| `https://api.carawaylabs.com/docs` | Interactive Swagger engineering/developer exploration, including Try It Out for approved public GETs | The same `/openapi.json` |
| `https://api.carawaylabs.com/redoc` | Retained lightweight read-only reference/fallback | The same `/openapi.json` |
| `https://carawaylabs.com/docs` | Canonical public human-facing Atlas help and developer guides; Fumadocs owned by Web #148 | Guides plus mechanically derived public API reference |

There is one API schema source of truth, not a hand-maintained endpoint reference.
OpenAPI `externalDocs` cross-links framework surfaces to the existing Atlas docs
destination. This is a docs-home link, not a claim that every planned developer
guide or generated Fumadocs API reference has shipped. Web #148 owns those releases.

## ReDoc decision and URL compatibility

Retain `/redoc` as a standard low-maintenance fallback. Do not remove or redirect
it in this change. API README, public guide/support documentation and production
deployment links already reference the existing API docs URLs. Native FastAPI
serves these routes directly; the API deployment uses its existing root-domain
HTTP routing, with no new proxy, ingress, deployment or environment setting.
Machine consumers continue to use `/openapi.json` without authentication or
redirects. Internal AI/RAG/profile/privacy/feedback capabilities remain absent
from all three public API documentation surfaces; runtime product routes remain.

A later ReDoc removal/redirect needs a downstream link inventory, a production-
ready Fumadocs reference destination, smoke evidence and a coordinated Web #148
release. Do not treat the docs home page alone as that readiness evidence.

## Fumadocs handoff and version/cache expectations

Fumadocs consumes **public `openapi.json`**, from the live stable URL or an exact
successful/released CI artifact described in [OpenAPI quality](openapi-quality.md).
Prefer immutable commit/checksummed input for reproducible builds; record its
source SHA and refresh/rebuild on an approved API release. Generate endpoint
reference mechanically; authored guides may explain semantics without copying
schema shapes. This API change documents the handoff; it edits no Web code.

The `/v1` HTTP contract and OpenAPI `info.version` application version are distinct.
Use the recorded release commit/checksum to identify a docs build; the application
version alone does not uniquely identify a schema revision. Live `/openapi.json`
reflects the running deployment and does not promise an immutable cache key,
ETag or fixed freshness lifetime. Native framework docs/schema responses retain
their existing cache behavior. Do not cache forever or infer data/publisher
freshness from documentation timestamps. Honor any actual HTTP cache headers;
pin immutable Git/CI input when repeatability is required.

`first-party-openapi.json` is a separate, complete, **unserved build artifact**
for existing Web application generators. It is not the public Fumadocs input.
Its existing destination is Web `contracts/openapi.json`; preserve both generator
inputs and do not replace the first-party file with the public projection.

URL smoke tests ensure JSON availability, both framework surfaces using only the
canonical schema, the docs-home cross-link, unchanged production server semantics
and no full-schema HTTP endpoint. No auth/token/SDK implementation or documentation
platform migration is part of this surface contract.
