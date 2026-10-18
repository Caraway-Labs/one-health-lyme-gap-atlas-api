# 0019: Typed successful selection availability

Status: Accepted for safe additive availability (product-owner authorization in API #237)
Date: 2026-10-17
Decision owner: Atlas product owner

## Context

Managed production ingress has replaced intentional origin503 errors with HTML504.
The owner explicitly approved revising availability semantics: a successful,
authoritative determination of known unavailability can be HTTP200. Failure to
establish authority remains an error. This does not fix or bypass ingress, and
does not authorize support requests, infrastructure changes, merge or deployment.

## Decision

Retain `/v1/observations`' existing `data/meta/links`, dated/context alternatives,
schema component names, operation ID, observations and required fields. Extend
the existing `CollectionMeta` with optional availability, reason, release_id and
limitations. Other collection producers omit these fields as before. No new
required fields or type renames are introduced for generated clients.

ObservationService populates `meta.availability=AVAILABLE` after successful
queries returning published records, including records whose publisher values
are unknown or missing. Availability means records are delivered, not biological
presence or adequate evidence. On the initial page of a successful non-CDC
release/measure/query lookup with no matching published rows, it returns200 with
`data=[]`, `availability=UNAVAILABLE`, `reason=NO_PUBLISHED_RESULTS`, the checked
release and explicit selection-only limitations. Use `Cache-Control: no-store`.
Empty continuation pages remain AVAILABLE with no unavailable reason. Existing
county-context initial missing-selection404 is retained. `reason` is omitted
when no unavailable reason is asserted.
An initial empty non-CDC selection rechecks the strict current release before
asserting NO_PUBLISHED_RESULTS. A pointer change or failed recheck remains503.
This prevents a current-view switch from disguising an old-release filter as
known absence. It adds one bounded metadata read on that path and is not a
claim of transactional consistency across all query statements.

No global exception mapping changes. CDC unknown coverage, admission or metadata
failure cannot use this success path. Pinned Data producer has no validated
unavailable discriminant; its UNKNOWN_SOURCE_COVERAGE rejection occurs before
complete consumer admission. See the committed classification table. Both CDC
measures, cumulative period, producer pin, publisher values and NO_RECORDS
semantics remain intact. Operational503 may still be rewritten by ingress.

## Geography compatibility decision

The existing geography resource is identity-only `ResourceEnvelope[Geography]`
with required non-null data; it is not a canonical geometry resource. Web's
generated TypeScript and Zod contracts rely on that shape. Returning null there
would be incompatible. The narrow compatible implementation is an additive
`/v1/geographies/{geography_type}/{geography_id}/availability`, following the
existing Tier1 pattern, that validates current identity/release before reporting
unpublished canonical identity delivery. The alternative was presented before
implementation and follows the owner's authorized compatible API scope; no
incompatible nullable union is introduced. It returns200 UNAVAILABLE with reason
CANONICAL_GEOGRAPHY_UNPUBLISHED, data=null, release_id and limitations. Only
counties are supported: malformed IDs400, absent current identity404,
unsupported state availability400, failed or inconsistent authority503. A fresh,
strict current-release read must match the cached county directory snapshot.
Duplicate/malformed counties and empty directory remain503. No-store applies.
No geometry is fabricated or evaluated. The legacy route is unchanged and still
has its ingress limitation; adopting the additive operation is necessary to
consume typed availability. An in-place migration requires separate review.

## Consequences and alternatives

Existing clients ignore added optional metadata and retain old model names.
Consumers wanting explicit availability must regenerate their pinned full
first-party contract and inspect meta; they must not interpret AVAILABLE as
positive evidence. Web code remains unchanged in this API PR. Existing errors,
pagination and query bounds stay authoritative.

Rejected: convert all503 to200; inspect private authority/exception messages to
infer availability; fabricate observations; change CDC admission; use a nullable
geography resource without compatibility review; infrastructure workaround.

## Acceptance, rollout and rollback

Focused and full regression suites, additive OpenAPI floor, regenerated public
and first-party exports, mypy/Ruff/changed-file formatting, package/Docker/security
checks, current hosted Quality and exact-head independent review are required.
Neither local tests nor CI prove public production behavior. After separate
human merge/deployment approval, run the bounded multi-host plan in
`docs/api-237-typed-availability.md`. Roll back through an approved API revert and
normal protected deployment; no Data migration or infrastructure rollback.

References: workspace ADR0002/0003, API ADR0017/0018,
`docs/api-237-availability-classification.md`, `docs/public-api-v1-contract.md`.
