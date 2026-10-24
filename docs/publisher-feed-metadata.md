# Publisher descriptions and labels: first-party API proposal

Owner direction `Sentinel_dafa6b0ae8108191b308b077e58b0f27` (2026-10-22
01:17 UTC) requests retaining feed-provided descriptions and category labels
for later UI choices. This change is separate from API #279's source catalog
union and does not change that query or remove its intended-reader proof gate.

`GET /v1/intelligence/feed` adds `publisher_metadata` containing only
`description` and `categories`. Description is null for historical records
without the new mapping. Eligible new descriptions carry `origin: publisher`,
`format: plain_text`, and `state: present|empty|not_provided`; text is respectively
nonempty, an empty string, or null. Empty is not absence. Text is a complete
sanitized plain-text representation of the RSS description, not generated text
or a shortened excerpt. HTML is not returned or executed. The unchanged 4096
character bound rejects oversize values; DATA must qualify real content before
admission. Categories are original publisher labels and can include spaces;
they do not establish validated scientific taxonomy or disease relevance.

The SQL explicitly projects only these two publisher properties. Private native
trees, rights receipts, raw XML, media, authors and other publisher properties
are excluded from this feed slice. `/items` V2 also accepts the optional new
description object. Pagination, revision state, filters, no-store and sanitized
503 failures retain the existing behavior. No WEB presentation is changed.

Deployment is gated on reviewed DATA schema/mapping compatibility and actual
intended-reader retrieval. API main deploys automatically, so merge requires
separate coordinated production approval. Mock/SQLite tests do not prove
Snowflake execution, live source admission or rendered UI delivery.
