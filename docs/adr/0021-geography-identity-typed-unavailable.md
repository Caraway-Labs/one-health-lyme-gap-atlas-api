# 0021: Geography identity route answers unpublished delivery as a typed HTTP 200

Status: Accepted (product-owner decision recorded in API #343, Epic #335)
Date: 2026-10-24
Decision owner: Atlas product owner
Supersedes: the "Geography compatibility decision" paragraph of
[ADR 0019](0019-typed-selection-availability.md), for the legacy identity route only.

## Context

`GET /v1/geographies/{geography_type}/{geography_id}` has always answered a
valid county or state FIPS with a permanent `503 CANONICAL_DATA_UNAVAILABLE`,
because canonical identity delivery is not published. It never queries Snowflake
and answers in about 3 ms.

DigitalOcean App Platform's ingress replaces every origin 5xx response with a
generic HTML `504` page, drops the body and keeps the original status only in
`x-do-orig-status`. Epic #335 proved this for the geography route (622 of 622
unthrottled calls on 2026-10-24, 32 of 32 matched to API logs at a median of
3 ms) and API #303 proved it for Ask Atlas. Consumers therefore never see the
typed 503, cannot distinguish a governed "not published" from a gateway
failure, and dashboards count it as an infrastructure fault. No provider-side
control exists (API #237) and the owner decided not to pursue a provider
request.

ADR 0019 added the additive `/availability` route as the compatible fix and
left the legacy route unchanged, because "web's generated TypeScript and Zod
contracts rely on" a non-null `data`. It said an in-place migration "requires
separate review".

## Decision

The product owner decided on 2026-10-24 (API #343) to apply the typed-200
pattern used for Ask Atlas (API #303) to the legacy identity route itself.

- For a valid identity the route returns HTTP 200 with the typed body
  `GeographyUnavailable`: `availability: UNAVAILABLE`, `reason:
  CANONICAL_GEOGRAPHY_UNPUBLISHED` (the vocabulary of the `/availability`
  route), `data: null`, the validated `geography` identity echoed back, and
  `limitations`. `Cache-Control: no-store`.
- The identity is validated for **shape only**; membership in the current
  release is not asserted and no `release_id` is returned, so the route stays
  instant and independent of the governed directory and Snowflake. Callers who
  need membership use `/availability` (counties only).
- Invalid identities remain `400 INVALID_REQUEST`, absent resources `404`, rate
  limiting `429`. Genuine unexpected failures remain 5xx. The route no longer
  has a deliberate 5xx.
- The success schema is documented as `anyOf [ResourceEnvelope[Geography],
  GeographyUnavailable]`: the former is what a future published resource will
  return, the latter is what the route returns today. `ResourceEnvelope[Geography]`
  keeps its name and shape, so nothing existing is renamed or made nullable.
- The permanent `503` and its `CANONICAL_DATA_UNAVAILABLE` mapping are removed
  from this route (and the dead special-case in the HTTP error handler).

## Compatibility review (the "separate review" ADR 0019 required)

- Web application source does not call the identity route; only tests and the
  public docs reference it (`tests/ux-reset-investigate.test.tsx` asserts it is
  *not* called, and the docs page carries a dated production-check badge). The
  web repo's pinned generated client still describes the old 503 and the
  non-null `data`; it is unaffected until it regenerates, and a follow-up in
  the web repo covers regeneration, the docs badge text and the union type.
- No MCP, ML, dataset-discovery or shared-python code calls the route.
- External consumers read the public OpenAPI: the 200 schema becomes a union and
  the 503 disappears. A typed 200 that is a documented, additive branch is a
  compatible change for readers that ignore unknown branches; clients that
  assumed a 503 must now read `availability`.
- The black-box harness and acceptance probes treated this route's 503/504 as
  the expected state; they must expect the typed 200 afterwards.

## Consequences and alternatives

Genuine operational 5xx anywhere else remain subject to the ingress rewrite;
that is tracked by the audit story under Epic #335. Rejected: leaving a
permanent 503 (masked, unreadable); changing the legacy route to 404 or 501
(still 5xx for 501, wrong meaning for 404); a nullable `ResourceEnvelope`
(breaks existing generated contracts); infrastructure workarounds.

## Rollout, verification and rollback

Both OpenAPI exports and the first-party contract fixture are regenerated in the
same PR. After deployment the read-only reproduction script
(`atlas_mcp_uat/geography_503_repro/geography_repro.py`) must show zero HTTP 504
on the geography routes and every response a typed 200 with
`x-do-orig-status: 200`. Rollback is a revert of the PR; no data or
infrastructure changes are involved.
