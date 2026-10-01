# Structured intelligence briefing v1 — API #90

Owner: API repository. Review state: implementation draft for independent parent
review. DATA #131 defines normalized intelligence; #535/#536 own ingestion,
identity and governed storage. These schemas preserve that accepted v1 item
shape. The four synthetic transport examples are copied unchanged from DATA
#131; they do not establish delivery of email/web or any live source.

`openapi.json` documents `BriefingGenerationRequest`, `IntelligenceSnapshot` and
`BriefingArtifact` as reusable component schemas. There is no new HTTP route,
subscriber account model, delivery provider, schedule or production wiring.
Existing public and authenticated route behavior is unchanged. WEB work is
excluded by the user's scope; generated client adoption requires its owning
repository's later coordination, rather than a hand-written client interface.

## Generation contract and interpretation

`BriefingService.generate(request, snapshot, generated_at=...)` is an internal,
deterministic generation path that can be reused by API #44. A trusted future
composition must supply a bounded snapshot from the approved DATA projection,
reviewed source metadata and a stable snapshot ID. Arbitrary client-uploaded
records cannot become authoritative evidence. The API validates consumer shapes
and missingness; it does not fetch, renormalize, recompute DATA publication hashes,
approve a registry version, or infer geography. DATA storage remains responsible
for reviewed rights, identities and actual acquisition receipts.

The request carries an opaque private subscriber key, preference revision,
digest version, geography/topic/source scope, exact UTC window and result bound.
It contains no address, raw account identifier, recipient list or consent claim.
The subscriber key must come from the owning approved preference composition;
an unkeyed hash of an address is not a privacy design. It is excluded from the
result artifact. Consent, frequency selection, suppression and deletion remain
the actual preference dependency of #44.

Daily and weekly generation use the same half-open `[start,end)` window, with
explicit UTC cutoffs. Windows are limited to 31 days. The window basis is the
accepted capture's `fetched_at`, never a substitute publication date. Delayed
publisher records can therefore enter a recent capture window while preserving
their older publication/update/event dates and field states. Arbitrary timestamp
fractional precision is retained. Input snapshots must reach the cutoff and
contain no capture after `as_of`; generation time cannot precede the snapshot.
This bounded snapshot is not proof of global publisher coverage or source health.

Matching is exact and case-sensitive: alternatives inside each selected dimension
are OR; selected dimensions are AND for each attributed capture. An empty scope
means all available records in the supplied snapshot and says so in `why_matched`.
Item geography/topic tags alone establish those matches. Source scope, author
affiliation and title keywords never become geography. Publisher/inferred origin
and inference method/version/confidence remain on the original evidence. Unknown
confidence stays unknown and does not establish alert eligibility.

Ranking v1 orders matching value count descending, actual publisher publication
or update recency descending, then stable item ID ascending. Missing publisher
dates remain unknown; fetch time is not a ranking substitute. Reviewed trust
categories are retained, but are deliberately unweighted: #44's final source
trust weighting needs its owning product review. This implementation makes no
materiality or confidence decisions for #45.

One ranked publication retains every matching source/version/transport/capture
and legitimate revision as typed evidence with complete original provenance,
chronology, limitations and a preference rationale. Exact duplicate inputs are
removed; repeated polls share one ranked article. Conflicting publisher revisions
remain separate evidence, without claiming the latest arrival is authoritative.
No synthetic summary or clinical claim is generated. Downstream summaries can
use only the separately attributed permitted publisher titles/excerpts. All
publisher text remains untrusted and must be rendered as text.

Every artifact is `mode=digest` with fixed periodic, non-alert language. Empty
results preserve scope/window/version and limitations; they do not imply absence
of disease. Alert mode is rejected. #45 owns any future alert schema and its
eligibility/materiality/confidence/cooldown decisions. Atlas deep links require
an actual owning route contract and are not invented here.

## Idempotency, bounds and failure behavior

Artifact identity binds subscriber key, exact window, preference revision,
digest version, contract version, generation version and digest mode. The input
checksum additionally binds every result-affecting preference, limit, snapshot
ID/time, normalized item, source metadata and snapshot limitation. Input ordering
and exact duplicate rows do not change semantics. Repeating the same unit returns
the first immutable artifact including its generation timestamp. Changed inputs
under that identity raise `BRIEFING_IDEMPOTENCY_CONFLICT`; a deliberate version or
preference revision change yields a distinct identity instead of overwriting it.

`BriefingArtifactStore.insert_if_absent` must atomically insert or return the
stored winner. Only an explicitly constructed in-memory test store is included;
it is not default production persistence or durable delivery deduplication.
Production storage/retention, intended writer authorization and concurrent
process/commit-replay proof remain integration gates. The existing API's read
role is not permission to write a digest ledger. No migration/grant is proposed.

Snapshots cap at 1,000 normalized captures/source descriptors and 10 MB canonical
JSON; output caps at 100 ranked publications. Input field/tag lengths and counts
are bounded. Omitted publication count is explicit. Missing/ambiguous source
versions, conflicting immutable captures, invalid cutoffs and unsafe fields fail
closed. Provider failures become fixed diagnostics without raw exception chains;
a returned store receipt must match the complete deterministic artifact, except
the retained first generation timestamp. Exact replay is safe after ambiguous
provider/commit outcomes, subject to the production store's required guarantee.

Grouping deduplicates within this window. Cross-window repeated delivery requires
#44's actual generated-item/send-attempt ledger and suppression rules; a repoll
must not be misrepresented as a new publisher revision. Source health/cadence and
actual quiet/failure context follow DATA #136 rather than fabricated metadata.

## Acceptance evidence and release gates

| API #90 acceptance | Implementation/evidence | Remaining gate |
| --- | --- | --- |
| Versioned OpenAPI schema | Exported request, snapshot, artifact and nested evidence schemas; additive-path contract test | Parent API contract review; owning consumer adoption |
| Provenance and preference rationale | Four DATA transport fixtures preserve all public fields; per-capture source/version and `why_matched` tests | Intended-role governed projection integration and actual preference revision |
| Same windows as #44 | One explicit cutoff path; daily/weekly, delayed publication and precise half-open boundary tests | Approved scheduling and frequency resolution in #44 |
| Limitations and non-alert language | Fixed digest notice, empty-window limits, no inferred geography/clinical claims; alert rejection | Real consumer demonstration and source-health context |
| Empty/dedupe/version idempotency | Empty snapshot, duplicate polls, cross-source revisions, reordered inputs, version/prefs conflicts and concurrent test-store tests | Durable production store/retention/commit-replay evidence |

API main was refreshed to `bbed404` after the API #85 deployment before this
isolated branch changed. Main pushes deploy production, so parent approval and
release coordination are mandatory before merge. This draft authorizes no live
source, source subscription, mailbox, email, LLM call or spending commitment.
Rollback is to omit these internal components from future composition; existing
released routes and storage are unchanged. Keep #90 open until the owning review,
integration and consumer evidence support its delivery claim.
