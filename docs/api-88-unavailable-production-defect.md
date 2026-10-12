# API #88: completed upstream 503 replaced at ingress

Production diagnosis: 2026-10-12 UTC. API #88 remains open until the public
negative acceptance request returns canonical 503 problem JSON.

## Correlated evidence

ACTIVE deployment `25d92f6e-1941-413e-8882-7e2409494862`, source
`31d9bad3b2274a5ef5ed3cffc6ba113fec03e53b`, app
`fb312ce3-e762-45b0-bf34-12d3df91eee6`, service `api`, region `sfo`.

Request: GET `/v1/observations`, measure_id=burgdorferi_status,
geography_type=county, geography_id=08013,
start_date=end_date=2025-12-31.

| Surface | Status/type | Elapsed | Correlation |
| --- | --- | --- | --- |
| Container localhost:8080, full live FastAPI path | 503 application/problem+json | client 2714ms; app log 2543ms | 4f503f25-2898-4c88-8a04-64e7a065d306 |
| Public api.onehealthatlas.org | 504 text/html | client 6656ms; app log 3036ms | 56768cb9-9d7e-4892-a2b4-8b737003fbf3 |

The public response preserves `x-do-orig-status: 503` and the sent X-Request-ID;
its server is Cloudflare, CF-Ray `a4926211ff115ea8-SEA`, and error-page marker
is `via_upstream (503 -)`. The corresponding application completion log records
503, not cancellation, deadline exhaustion or an unhandled exception. This
is replacement of a completed upstream error response, not an application
query timeout. It happens after FastAPI and the request middleware.

The configured/default public Snowflake statement timeout is 15 seconds.
The CDC producer retains its min(API timeout, 30 seconds) cap. The 5-second
DigitalOcean readiness-probe timeout applies to `/health/ready`, not these
observation requests. No timeout is increased by this change.

Ingress currently matches `/` to `api`, port 8080; no custom error page or
maintenance mode is configured. No private SQL, credential or lineage detail
appears in either response. The same producer/source authorization remains.

## Bounded application correction

Before this correction, ObservationService called CURRENT_RELEASE_V before
CDCObservationAdapter checked county membership. That accounted for an
unnecessary approximately 2.5–3-second Snowflake-backed path for a request
whose unsupported membership is already known locally.

The service now invokes the existing adapter's extracted request checks before
reading the release. The adapter repeats those checks on direct use and retains
the exact release check before producer reads. Supported requests still query
the current release and undergo unchanged Data admission. Generic observations,
request checkpoints, timeouts, authentication and exception handling are intact.
No API shape or scientific classification is changed. Tests require zero
repository/producer calls for unsupported or mixed county memberships and the
same sanitized canonical 503 problem response.

The production probe copies each recorded request, records elapsed time, checks
the negative problem content type/body, and reports gateway status without
printing the error page. It never accepts 504 as successful 503 acceptance.

This query correction is useful independently, but **does not assert that the
provider's 503 replacement is fixed**. Production acceptance must be repeated
after reviewed merge/deployment and any supported provider correction.

## External owner action

Owner: Atlas DigitalOcean account/team deployment administrator, working with
DigitalOcean App Platform support (provider-owned ingress/Cloudflare policy).
Use the existing support channel; no support message was sent by this task.

Ask support to enable or identify a **supported origin-error passthrough** for
the app/service above: preserve completed upstream HTTP 503, its
application/problem+json content type, and its original body. Supply the
request IDs, timestamps from application logs, CF-Ray and deployment identity
above. Require a per-app supported setting and verify it with the real request.

There is no documented origin-5xx passthrough setting in the reviewed
[app-spec reference](https://docs.digitalocean.com/products/app-platform/reference/app-spec/).
`ingress.custom_error_page_url` embeds an HTML page when unreachable; it cannot
preserve canonical JSON. `disable_edge_cache` controls caching and does not
document disabling response substitution; do not treat it as a proven fix.
Do not invent a spec field or deploy a new proxy/topology to bypass approval.

A [DigitalOcean-hosted customer report](https://ideas.digitalocean.com/app-platform/p/option-to-pass-application-5xx-responses-through-the-app-platform-ingress-unchan)
describes the same x-do-orig-status behavior and reports support saying it is
enforced by Cloudflare and cannot be changed per app. That is corroboration,
not a support decision for this account. Obtain the provider's definitive
answer. If no supported passthrough exists, the owner must record the release
decision; this task does not weaken HTTP semantics or redesign deployment.

## Final gate

Run `uv run python scripts/probe_cdc_api.py` against the exact ACTIVE reviewed
commit. Require three HTTP 200 admitted publisher/evidence results and public
08013 HTTP 503 application/problem+json. Post genuine sanitized output on #88,
then close and clean up only after all four pass. The existing CDC/Web handoff
is in [api-88-production-cdc.md](api-88-production-cdc.md).
