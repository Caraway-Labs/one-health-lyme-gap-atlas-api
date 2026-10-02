# API request trace contract (#159)

The existing FastAPI OpenTelemetry SERVER span is the canonical HTTP request
boundary, including `/health/live`, CORS preflight, validation, unmatched routes,
and middleware rejections. No second request root is created. Its duration ends
when the final response body is sent; completion-log duration measures middleware
processing before body transport and must not be substituted for trace duration.

| Attribute | Values and purpose |
| --- | --- |
| `request.id` | Existing accepted `X-Request-ID` (ASCII vocabulary, 1–80 characters), otherwise generated UUID; direct correlation only |
| `http.route` | Registered route template, bounded public-detail template, or `unmatched`; never the requested URL |
| `http.method` | GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS, TRACE, OTHER |
| `http.status_code` | HTTP 100–599 |
| `atlas.request.status_class` | 1xx–5xx |
| `atlas.request.outcome` | success, client_error, server_error, cancelled |
| `atlas.request.failure_class` | none, http_client_error, http_server_error, unhandled_error, cancelled; timeout/dependency_error reserved for classified boundaries |
| `atlas.telemetry.schema_version` | Source-controlled `1` |

`api_request_completed` and `api_request_failed` use the same method, path,
status_code, status_class, outcome, failure_class dimensions plus `request_id`.
Request IDs are never group-by labels. HTTP status is separate from Ask Atlas
product/service outcomes on `knowledge_chat.service` / `atlas.ask_atlas.*` (PR158).
No arbitrary configured version/model identifiers are added to request dimensions.

`PrivateInstrumentationProvider` adapts only FastAPI instrumentation and delegates
to the existing shared tracing provider. It filters initial attributes before
sampling/export, subsequent attributes, automatic headers, exception events and
status descriptions; no URLs, query values, IPs, credentials, bodies, exception
messages or stack payloads reach these spans. W3C parent context remains intact.
Send spans are internal transport children, never additional HTTP requests.

Tracing start/attribute/end errors are optional; original API errors and
cancellation propagate. No new exporter, backend, sampling or public schema is
introduced. Existing stored/tail-sampled traces are not an unbiased population
for volume, error ratios or latency quantiles. Rollout and rollback use the
existing quality-gated deployment workflow after independent review.
