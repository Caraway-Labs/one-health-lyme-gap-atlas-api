# API #237 / #247 typed availability handoff

Baseline `af70c64077aa94e89622b485cf3a2b0eecf47007`, isolated branch
`codex/api-237-typed-availability`. No edits in the investigation, API248, Data
or Web checkouts. See the [503 classification](api-237-availability-classification.md)
and [ADR0019](adr/0019-typed-selection-availability.md).

## Observation contract

`/v1/observations` keeps its existing data/meta/links envelope and schemas. New
optional CollectionMeta properties are populated only by ObservationService:

| Field | Meaning |
|---|---|
| availability | AVAILABLE records/page, or UNAVAILABLE after successful initial non-CDC query with no published rows |
| reason | NO_PUBLISHED_RESULTS only for known-empty initial selections; omitted otherwise |
| release_id | Release actually established by the repository, not a configured guess |
| limitations | Selection-only caveats; never a claim of biological absence/source coverage or geography membership |

`data=[]`, normal response_at, links and exhausted next_page_token remain. Known
unavailable successes use no-store. Existing catalogs do not start emitting
observation availability metadata. Available rows, value states, provenance,
pagination token bindings and cumulative CDC dates are preserved. An empty
continuation means page exhaustion rather than unavailable scientific evidence.
Before asserting an empty initial selection, the service revalidates the current
release. A changed pointer or failed recheck remains503. This adds one bounded
release read only for that empty-success path; it is pointer revalidation, not
a multi-statement transactional snapshot guarantee.

Operational errors remain RFC9457 non-2xx, including repository/authentication,
SQL timeout, unavailable current release, source/admission mismatch and malformed
or duplicated CDC authority. Existing400/404/422/429 remain. No global handler
rewrites errors. Actual unknown-source coverage is **blocked**: pinned Data
commit2476bfec has no successful consumer-safe availability outcome. Missing
admission is not an empty scientific selection. A new reviewed Data contract is
required; this PR does not modify Data or claim that county02122 qualifies.

## Geography and Web integration

The current geography schema represents identity and label, not polygons. Its
required `data: Geography` cannot safely become null without an explicit
compatibility decision. The additive
`/v1/geographies/{geography_type}/{geography_id}/availability` preserves the old
resource. The product owner explicitly approved this additive Option1 and
preserving the current route's non-null successful contract. County availability checks
real current FIPS membership, rejects duplicate/malformed directory entries and
checks a fresh strict current-release read against the cached directory snapshot.
It returns200 UNAVAILABLE, reason CANONICAL_GEOGRAPHY_UNPUBLISHED, data=null,
the checked release and limitations. No geometry is evaluated or published.
Malformed IDs400, absent current identities404, unsupported states400 and
dependency/inconsistent-authority503 remain errors. No-store applies. One fresh
bounded LIMIT2 release query/connection is added even on snapshot cache hits.
The legacy route retains its503/ingress symptom; this increment supplies a
compatible alternative, not an in-place fix. Both issues remain open pending
owner-approved adoption and actual production acceptance.

Web read-only inspection found generated `ResourceEnvelopeGeography.data`
non-null, and `resolveCountyIdentity` dereferences the Geography. Current county
navigation uses the published score directory because canonical geography is
pending; map rendering uses `/v1/atlas/geometry` and forbids canonical/analysis
paths. No map contract or Web code changes belong here.

Existing generated observation collection models accept the optional meta
addition; current Zod objects strip unrecognized fields, so a pinned client
continues working but will not expose new availability until regenerated.
The new geography operation generates a separate result with data:null. Existing
ResourceEnvelopeGeography consumers are unchanged; callers adopting availability
must not pass its null data to resolveCountyIdentity.
Integration: copy API `first-party-openapi.json` to Web's pinned
`contracts/openapi.json` in a separately authorized Web change, run its existing
`npm run generate:api`, and narrow dated/context results as required by ADR0018.
Preserve existing generated component names. Do not replace first-party input
with the public projection. New availability consumers must distinguish record
availability from each record's value_state and normalized unknown.

## Bounded postdeployment acceptance (not executed)

Only after explicit human merge/deployment approval: record exact ACTIVE
deployment/source and release, then serial probes across api.onehealthatlas.org,
api.carawaylabs.com and the supported direct App Platform hostname. Use explicit
10s connection/35s total timeouts, no retries, at least0.3s between completions,
respect Retry-After, max12 requests in the initial cohort, no scans/load tests.

Suggested initial cohort per hostname: one source-backed available observation,
one known-empty ordinary selection after proving current release/measure scope,
one pending geography availability request only if approved/implemented, and
one invalid-filter control. Adjust cohort to available implemented paths before
running; never probe a proposed endpoint as if deployed. Later bounded approval
can cover current geography identity404, CT planning region and other families.
CDC known-unavailable200 cannot be accepted until a separately reviewed Data
contract supplies authoritative classification. Revalidate current admission;
02122 is not a permanent unavailable fixture. Actual admitted CDC/NO_RECORDS,
human, climate and county-context successes must preserve values and provenance.

Do not induce outages. Use existing natural operational failures/logs and local
failure injection for genuine503/504 distinctions. Never claim genuine503 JSON
passthrough: ingress behavior is unchanged. Preserve request correlation and
privacy; do not publish private lineage/credentials. Keep both issues open until
their actual acceptance criteria pass in production.

## Validation and rollback

Local and hosted evidence is reported in the PR and issue comments at the exact
implementation SHA. No production evidence is generated during implementation.
Independent review must assess Data classification, nullable geography risk,
cache behavior, schema/client compatibility and failure regressions. Human
review, green current Quality and explicit merge/deployment authority remain
separate gates. Rollback is an owner-approved revert of API code and both schema
exports through normal protected release; consumers tolerate disappearance of
optional meta fields. No Data, domain, provider ticket or infrastructure action.
