# Gap Score v1.1 coordinated release (#32)

API consumes Shared Python package 2.0.0 by immutable commit. Scores are computed
at request time from the cached raw governed snapshot; no Snowflake regeneration,
new data, grants, or ML changes are involved. Default smooth_k=50 is authoritative.
Explicit alternative settings are scenarios. `low_incidence_breakpoint` remains
visible as deprecated but returns 422; it is never reinterpreted as k.

`scoring_methodology_version=surveillance-gap-score-v1.1` is carried by score
collections, county rows/details and metadata. `methodology_version` and
`release_id` retain their original data-release meanings. Score settings carry
smooth_k, ecological_share and missing_human_weakness. Bulk rows now include the
actual governed 2023 minimum incidence, SVI/insurance percentiles and RUCC for
same-input scenario parity; null is not zero. No inferred incidence is allowed.

CSV uses the same service scorer/order/colors as county details. CSV includes
scoring version and k. PDF provenance includes scoring version/settings and its
existing limitations rendering names v1.1 and k without altering immutable Typst
templates. Raw snapshot caching never caches calculated results across settings;
HTTP ETags hash the complete versioned payload and report cache keys include
provenance/settings. All ML routes and persisted model outputs are unchanged.

## Promotion

1. Green Shared Python PR #33 and exact immutable pin, independent review.
2. Green API and Web PRs, generated first-party and public OpenAPI parity, full
   snapshot Python/TypeScript tests, E2E, current-main branch checks.
3. Merge/promotion through existing protected main workflows in dependency order.
   Existing Web requests with breakpoint fail explicitly during API cutover;
   updated Web fails closed on missing/wrong scoring metadata. Refresh clients
   and wait out the prior 300-second HTTP cache before acceptance. No unlabelled
   mixed score experience is allowed. Do not bypass any existing review safeguard.
4. Verify ACTIVE source hashes, score collection/detail/CSV equality and browser
   Explorer/Compare/What-if/PDF with exact release and method identities.
   Include NY 36027, MA 25007/25019, ME, PA, emerging-area and missing-human
   examples. Verify all three ML outputs remain on their original releases.

## Rollback

Retain pre-release API main 238a61fe58a151020286d4efd43f73ae66fcb5e2 and Web main
5ada80eddfda4d6acd476b70d10b1c28c0a478e2 as source references, and capture the actual
prior ACTIVE deployment IDs before promotion. Revert the coordinated Web/API PRs
through protected PR/CI. API reverts the shared pin to
83ccbe76047185f6aacaff551afd8b03d88aa5cf; no data rollback. Updated Web rejects
legacy API scoring until its own rollback finishes. Verify ACTIVE exact revert
SHAs, invalidate/wait cache TTL, reload and verify historical outputs. Do not
use an old workflow run that the latest-main safeguard will skip.

Production acceptance remains pending until actual deployment evidence exists.
