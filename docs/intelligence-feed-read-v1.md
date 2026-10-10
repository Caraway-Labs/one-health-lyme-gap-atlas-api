# API172 intelligence feed read projection

First-party routes `/v1/intelligence/items` and `/v1/intelligence/sources` read only the governed DATA `PRESENTATION.INTELLIGENCE_FEED_V`. They are excluded from the external public analytics OpenAPI allowlist. The complete first-party artifact contains stable operation IDs for downstream generated clients; no WEB changes are included.

`INTELLIGENCE_FEED_READ_ENABLED` defaults false. Do not enable before intended API runtime-role readback, DATA publication acceptance and coordinated production release. Disabled or unavailable returns ProblemDetails 503, never a fabricated empty feed. Only exact DEV/PROD presentation databases are accepted. Bound parameters, explicit public columns, page/result limits, query timeout, rate/concurrency/query/body middleware and strict nested response validation protect reads. No private source registry, raw bytes, mailbox or credentials are exposed.

Items retain all source/revision attribution and publisher versus fetch dates, including missingness and untrusted-content labels. Inclusive publication-date filters support archive reads; there is no metadata retention cutoff. Stable identity ordering does not rank relevance. Sources describe only identities represented by published items, not all registered/active sources. Health is explicitly unavailable until DATA publishes a governed health projection; fetch dates do not imply operational health or disease activity.

Continuation tokens bind filters, offset and a state-change detector. Snowflake HASH_AGG is a noncryptographic 64-bit aggregate; pre/post checks and signed tokens detect ordinary changes but do not establish immutable snapshot isolation or cryptographic completeness. Restart pagination on detected change. Count ceilings bound returned data, not warehouse scan cost; the existing timeout applies. Live SQL and grants require intended-role verification.

Source handoff: DATA PR576 selection SHA `91cf14df2da6986a89c892a91aaf0faee3652d7bf8fcf56bce214fe316d3010c`, eight approved/three deferred, daily cadence. PubMed broad search version v2 fixes Babesia/babesiosis and Ehrlichia/ehrlichiosis. Exact generated PubMed RSS remains unresolved; NIH normal transport returned403. No denial bypass or source activation.

Matthew approved long-term normalized metadata/provenance subject to source permissions, recent UI default plus archive access, raw feed copies30days for debug/replay, and separate rights for excerpts/full article text. No blanket full-text copying approval. UI display choices belong to the frontend owner; this API imposes no UI window or deletion rule. DATA raw expiry/cache enforcement remains a live activation gate.

Shared files with climate API PR171: config and the generated first-party OpenAPI artifact. Reconcile fresh main and regenerate the complete artifact before merge. API main deploys automatically after quality checks: parent production coordination is required. API172 stays open for actual publication, health contract, runtime proof and production acceptance.

V2 companion to DATA578: the feed response accepts strict canonical item2.0.0 projections with publisher_metadata and empty derived_metadata, while API90/44 remain strictv1. `INTELLIGENCE_FEED_PROJECTION_VERSION=v1` remains the default; `v2` selects only the proposed `INTELLIGENCE_FEED_V2`. No view fallback or automatic enablement. Public SQL/typed responses exclude private native trees, raw publisher strings, XML/checkpoints, policy receipts and arbitrary derived values. Negative tests reject these fields and private/token-bearing media references. DATA v2 projection SQL is an unapplied review template requiring shared migration reservation and intended API-role proof. The new private native metadata allows future reviewed projections without historical re-ingestion; no paid enrichment runs. Real CDC fixture evidence is metadata-only, with NIH/PubMed still unresolved. Complete raw-copy expiry remains an activation gate.


## Metadata-only feed slice (API #172)

`GET /v1/intelligence/feed?source_id=cdc-eid-expedited&page_size=100`
reads **only** `PRESENTATION.INTELLIGENCE_FEED_V2`, independently of the legacy
`INTELLIGENCE_FEED_PROJECTION_VERSION` setting. This additive first-party route
preserves `/items`, `/sources`, and API #90/#44 compatibility. It uses the same
read-enable flag, parameter binding, query/result/page ceilings, state-bound
pagination, request middleware, dependency telemetry, and no-store policy.
No ingestion, data migrations, registry changes, grants, or infrastructure are added.

Each entry exposes only title, publisher, canonical original URL, publication UTC
when known, its explicit publisher-date state, and source/revision/retrieval/contract
provenance and limitations. SQL does not select excerpts, bodies, descriptions,
media or private native metadata. Missing/invalid/withheld publication dates remain
null; fetch time never substitutes for them. Inclusive publication filters exclude
unknown dates. Identity/revision ordering is deterministic, not a claim of recency
ranking. An available empty view returns 200 with `data: []` and no continuation;
disabled, inaccessible or invalid projections return a sanitized 503.
Source health remains explicitly unavailable, independently of the feed result.

### Release route and acceptance

There is no separate API DEV hosting/promotion workflow in this repository.
Validate this branch locally against the DEV projection before merging; a local
CLI-backed HTTP read is not proof of the deployed service's key-pair identity.
The installed `snow` named PAT connection `ATLAS_DEV_READ` uses
`OH_LYME_DEV_READ`. For this session its accessible existing warehouse is
`OH_LYME_DEV_INGEST_XS_WH`; `COMPUTE_WH` could not be selected under that role.
Validate user/role/database/warehouse before each live acceptance session.

Production follows `.github/workflows/quality-deploy.yml`: approval and merge to
`main`, required `quality`, then DigitalOcean deployment of that exact latest-main
SHA to the existing `api` service. Manual `workflow_dispatch` on main with
`deploy_production=true` redeploys the current main tip. The deploy job must prove
`ACTIVE` and matching `source_commit_hash`; see `ci-and-production-deploy.md`.

Before activation, Data must independently publish accepted CDC EID V2 rows in
PROD with its required retention/access controls and existing service-role read
access. API runtime uses `OH_LYME_API_SVC` / `OH_LYME_PROD_READ`, key pair,
`SNOWFLAKE_DATABASE=ONE_HEALTH_LYME_GAP_ATLAS_PROD`,
`SNOWFLAKE_PRESENTATION_DATABASE=ONE_HEALTH_LYME_GAP_ATLAS_PROD`,
`SNOWFLAKE_PRESENTATION_SCHEMA=PRESENTATION`, and its approved warehouse.
Set the existing server-only `INTELLIGENCE_FEED_READ_ENABLED=true` only after
those gates and runtime-role readback. `/feed` needs no projection-version flag.
Verify `https://api.carawaylabs.com/v1/intelligence/feed?source_id=cdc-eid-expedited&page_size=1`
returns 200 with a real title, original CDC link and provenance; empty results do
not satisfy live article acceptance. Rollback activation with the existing read
flag; code rollback follows the documented main-revert route.


### 2026-10-09 DEV evidence

The bounded named-CLI readback exercised the actual `SnowflakeFeedRepository`
SQL (count/state, bounded rows, state recheck), `FeedService`, and HTTP response
using `ATLAS_DEV_READ` in DEV. It returned HTTP 200 with a real
`cdc-eid-expedited` registry version 1 entry:

- Title: Importation-Driven Measles Resurgence and the Fragility of Elimination, Japan, 2024–2026
- Original URL: https://wwwnc.cdc.gov/eid/article/32/11/26-1187_article
- Publisher: Centers for Disease Control and Prevention
- Publication UTC: `2026-09-29T04:00:00Z`; state `present`
- Retrieved UTC: `2026-10-10T02:26:51.448954Z`
- Acquisition run: `f77622ad-db14-4ee6-ab0f-dcd6970d278e`
- Artifact SHA256: `d5157794b405c94455703d42bf580bcf46a1b6c4d1f9801d49bbbf3b1a414366`

This is real DEV-backed API read evidence using the named PAT CLI transport,
not a fixture and not production key-pair/runtime identity proof. The earlier
bounded read returned no rows while the separately executed acquisition was
still progressing; empty did not become an error or fabricate an article.
Production's existing API deployment was ACTIVE at
`89485b2f00aea32929694be8c02177c42b4b2fe1`. Read-only App Platform inspection
confirmed PROD databases, `OH_LYME_PROD_READ`, and `COMPUTE_WH`; the intelligence
read-enable flag was absent (effective default false). The existing production
environment already has `DIGITALOCEAN_ACCESS_TOKEN` and `DIGITALOCEAN_APP_ID`.
PROD projection/data/service-role acceptance was not inspected or mutated.
