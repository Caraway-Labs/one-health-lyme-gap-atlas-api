# Offline intelligence digest message rendering

This is a bounded API #44 preparation slice over API #90's frozen briefing
artifact. It is not scheduled delivery. No HTTP route, subscriber lookup,
credentials, provider calls, addresses, scheduler or send ledger is introduced.
Keep #44 open until its delivery and production acceptance evidence exists.

`DigestRenderer` requires an injected artifact resolver and exact prerequisites:
artifact identity, input checksum, preference revision and digest version. The
resolver must read the approved immutable artifact store; there is no default
resolver or live storage composition. These prerequisites authorize neither
subscriber consent nor delivery. A missing artifact, resolver failure or binding
mismatch fails closed. Resolver failures have finite diagnostics without the raw
exception chain. Validation is repeated even for an existing Pydantic instance,
because `model_copy` can bypass validation.

The immutable `RenderedDigest` contains a fixed subject, plain text and escaped
HTML, artifact/input identity, version bindings and a deterministic content
checksum. Repeating rendering produces the same message, not evidence of send
idempotency. A provider can later consume either representation without changing
briefing generation. There is no provider adapter or fake delivery implementation
in this slice, and no claim of an audited successful delivery.

Both representations preserve all ranked evidence captures: publisher/source
and registry version, source classification, title, explicitly untrusted
publisher excerpt, canonical citation or explicit unknown, publication/update/
event/retrieval times and missingness, preference match explanations, geography/
topic tags and their inference evidence, normalized identity/revision/hash,
transport, full acquisition/parser provenance and item/digest limitations.
The window remains explicitly based on capture retrieval time. Unknown publisher
dates stay unknown. No clinical summary, geography inference, trust weighting or
Atlas deep link is invented. Empty and truncated artifacts retain the fixed
non-alert notice, window, scope, counts and limitations.

All publisher text and URLs are escaped for HTML text and attributes. Links use
the accepted #90 HTTPS canonical-URL validation. There are no images, remote
styles, script elements or automatically fetched links. The subject is constant,
so publisher text cannot inject mail headers. Limits are 100 ranked publications,
1,000 evidence captures, 100 artifact limitations and 1 MiB UTF-8 per body. An
oversize body fails without trimming evidence or attribution. These are offline
engineering bounds, not a selected provider's policy or a production delivery
size decision.

## Acceptance and remaining gates

| #44 acceptance | This slice | Still required |
| --- | --- | --- |
| Daily/weekly personalized test digests | Existing #90 generation plus one-day/seven-day rendering tests | Approved frequency/preferences storage and scheduler |
| Same window/version avoids duplicate sends | Deterministic artifact-bound message checksum | Durable claim/send ledger, provider idempotency and ambiguous-commit reconciliation |
| Unsubscribed/suppressed never sent | No sending path exists | Authoritative consent/suppression contract and atomic recheck immediately before provider submission |
| Every item preserves provenance | Complete ranked capture evidence and citations in both bodies | Intended-role live store and consumer evidence |
| Observable/retryable provider failures | Resolver errors fail closed with finite diagnostics | Approved provider, receipt/status mapping, retry budget, suppression and send-ledger integration |
| Matching/ranking/deduplication tests | Reviewed #90 tests inherited; rendering checks preserve explanations | Product decision on source trust weights, clinical summary/deep-link ownership if needed |

Any future provider implementation must be explicitly injected, must require an
approved delivery authority that resolves current preferences and suppression,
and must bind a durable delivery claim to this artifact and renderer version.
Do not treat the rendering prerequisites or content checksum as consent, a send
claim, an unsubscribe token or a delivery receipt. Provider-specific work waits
for those contracts and parent production coordination. There are no automatic
retries or recipient-bearing diagnostics here.

Historical digest generation remains dependent on a frozen snapshot or an
approved as-of projection: the current DATA feed view alone cannot reconstruct
an earlier fetched-at window. This renderer does not query that view or refetch
publisher content.
