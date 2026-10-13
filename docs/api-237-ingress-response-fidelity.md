# API #237: structured origin 503 response fidelity

## Diagnosis

Production evidence attached to [API #237](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/237)
shows App Platform ingress returning HTML 504 with `x-do-orig-status: 503`
for application requests that completed as structured 503. Reproductions on
both public hostnames returned the same replacement in roughly 6 seconds. The
earlier #232 invalid-selector reproduction was correlated with application 503
logs at 5–6 ms. That rules out a request that simply exceeded the API's normal
timeout as the cause of those samples.

The tracked `.do/app.yaml` routes the service on port 8080 and sets 5-second
health-check timeouts; those health-check values do not govern observation
requests. It has no custom error page or ingress error-handling override. The
current DigitalOcean App Spec documents ingress rules for path/authority
matching, component routing, path rewrites, redirects, and CORS. It documents
`custom_error_page_url` as an HTML page embedded when the app is unreachable,
not as an origin-response passthrough. It does not document a control for
passing through origin 5xx status, headers, and body unchanged. See the
[App Spec reference](https://docs.digitalocean.com/products/app-platform/reference/app-spec/)
(last verified there on 2026-09-01).

Other documented settings do not establish a safe response-fidelity fix:

- `disable_edge_cache` disables the CDN cache. The spec does not say that it
  bypasses the ingress/Cloudflare response path or changes 5xx substitution.
  Ask DigitalOcean whether that setting changes this exact behavior before
  treating it as a candidate; any trial still needs approval and real
  multi-host verification.
- `maintenance` displays an app-wide maintenance message; it cannot preserve
  per-request API problem details.
- `secure_header` blocks requests without a required incoming header; it is an
  access control, not an origin-response setting.
- `custom_error_page_url` changes the displayed error page when the app is
  unreachable. HTML in an iframe cannot satisfy an API JSON response contract.

Account-level provider configuration is outside this repository and was not
changed or verified here. No documented, supported existing-topology setting
currently establishes origin 5xx passthrough. Obtain provider confirmation of
a supported control. If none exists, the minimum owner decision is to approve
or reject a topology/contract change that can preserve HTTP semantics; do not
invent an App Spec property or disguise unavailable results as successful.

The defect therefore cannot be repaired by changing the FastAPI response
handler alone. Changing a correct origin 503 to 200 or another status would
misrepresent unavailable data and is expressly out of scope. No timeout,
rate-limit, security, ingress, or deployment setting is changed by this PR.

## Bounded application change

Every ordinary application HTTP 503 now carries stable code
`SERVICE_UNAVAILABLE`; pending canonical geography keeps its existing more
specific `CANONICAL_DATA_UNAVAILABLE`. Existing `Retry-After` headers are
preserved as supplied, and request IDs remain correlated between the body and
`X-Request-ID`. Unknown CDC admission still fails closed and intelligence reads
remain disabled when configured off. No source admission or public data state
changes.

Failure-mode tests cover the origin contracts and ensure the existing CDC
acceptance probe rejects an ingress 504 even when `x-do-orig-status` reports
503. The probe reports status, content type, and origin-status metadata but
never prints an HTML error page. These tests verify the application and probe;
they do not demonstrate that production ingress has been corrected.

## Remaining approval and production gates

The actual fidelity fix requires DigitalOcean to identify a supported
origin-error pass-through control, or the owner to record an approved
topology/contract decision. If neither exists, the owner must decide how to
proceed without falsifying HTTP semantics. Do not add an undocumented App Spec
field or deploy an alternate proxy as a workaround.

After that decision and any separately approved provider change, run the
read-only negative and healthy-control requests against both public hostnames.
Correlate each result with the application completion log and record deployment
identity, response status/content type, request ID, `x-do-orig-status` when
present, elapsed time, and whether the response body is the expected RFC 9457
problem. Keep all four cases distinct: origin 503, ingress replacement, healthy
200, and transport failure. Do not claim production acceptance from unit tests
or hosted CI. Retain independent review, current quality, protected deployment,
production verification, and rollback review as separate gates.

## Relationship to API #234

The pending geography endpoint remains a local, fail-closed structured 503 and
continues to use `CANONICAL_DATA_UNAVAILABLE`. #234 can reuse this PR's stable
503 handling and failure-mode tests, but it cannot claim its public-ingress
acceptance criterion until the shared provider/topology issue is resolved and
verified. This PR does not change geometry behavior or expose analysis geometry.
