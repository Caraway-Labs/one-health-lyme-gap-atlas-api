# API writing style and shared vocabulary

This guide is the shared source for public API wording and definitions. Reuse
the definitions below instead of introducing competing meanings. It describes
the current governed contract; it does not change routes, data, or interpretation.

## Audience and tone

Write for developers, analysts, public-health practitioners, and people who
need to understand what an API response supports. Assume readers know HTTP and
JSON, but explain Atlas-specific terms. Be direct, calm, precise, and respectful.
Describe the available action or result before its limits.

## Sentence rules

- Lead with what the reader can do. Use active voice and `You can` or an
  imperative verb; never write `the consumer shall`.
- Aim for fewer than 25 words per sentence and fewer than four sentences per
  paragraph. Use one idea per bullet.
- Define terms at first use by linking to this guide or giving a short
  explanation. Prefer familiar words over internal implementation language.
- State a safety limit once per operation. Link to the operation's shared
  interpretation note or `info.description` for detail; do not repeat caveats
  in a chain of negative statements.
- Say what a response contains, when it is available, and what a client should
  do next. Do not imply that absent data is zero or evidence of absence.

## Approved safety wording

Use these sentences consistently when applicable:

- **Diagnosis:** “Atlas results are not a diagnosis.”
- **Individual risk:** “Atlas results are not an estimate of an individual's
  risk.”
- **Freshness:** “The response time records when Atlas built this response; it
  does not show when the publisher last updated the data.”

Include the relevant statement once in an operation description or a linked
shared interpretation section. Do not add these warnings to unrelated
operations or weaken their meaning.

## Internal terms: replace with reader language

| Avoid | Prefer |
| --- | --- |
| pre-execution logical-result ceiling | result limit; state the limit and that the request is rejected before data access when relevant |
| admitted Data delivery | describe the published observations or records that are available |
| persisted | published for an API result; stored for a storage action |
| typed 200 | describe the HTTP 200 response body and its availability value |
| retained / existing legacy wording | describe what the route returns or how it behaves |
| Fumadocs, `/docs`, and `/redoc` consume… | remove from public API `info`; document tooling in contributor guidance |
| logical-result ceiling | result limit |
| consumer-safe | suitable for this API response |
| producer-native | supplied by the source |
| resource503 | HTTP 503 response |
| allowlist (for data availability) | supported list, when that is the actual meaning |
| fail closed | return the documented error when required evidence is unavailable |
| no-store (for user-facing explanation) | tell clients that the response must not be cached |
| typed availability | availability field and its documented values |
| current release (without context) | current published data release |

Do not remove precise technical terms when they identify a real contract
element, such as HTTP 503, RFC 9457, `release_id`, or `page_token`. Explain
them plainly at first use.

## Shared vocabulary

Definitions below use the canonical public V1 contract as their authority.
Use the linked sections when the term is governed there. Do not broaden a
definition beyond the fields and behavior that contract specifies.

| Term | Plain-language definition | Contract source |
| --- | --- | --- |
| `release_id` | Identifier for the published data release used to produce a result. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `value_state` | Label describing how to interpret an observation's value, including observed, zero, missing, suppressed, unavailable, or specified no-record states. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `vintage` | The reference year or period for a dataset version; it does not by itself mean the measure is annual. | [County context](public-api-v1-contract.md#resources-and-semantics) |
| FIPS | A geographic identifier; county IDs have five characters and state IDs have two, with leading zeros preserved. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `measure` | A distinct variable that can be selected in an observation query. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `indicator` | A public-health concept that can group or describe related measures. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `methodology` | The method used to produce or interpret data; its identifier and version describe the method. | [Source and methodology resources](public-api-v1-contract.md#source-and-methodology-resources-55) |
| `provenance` | Information identifying the source and processing context for a result. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `limitations` | Factual qualifications that describe what a result does not establish or where its evidence is bounded. | [Typed observation selection availability](public-api-v1-contract.md#typed-observation-selection-availability-api-237) |
| `availability` | A field describing whether the requested selection has published results under the documented conditions. It does not describe biological presence. | [Typed observation selection availability](public-api-v1-contract.md#typed-observation-selection-availability-api-237) |
| `page_token` | Opaque value sent to request the next page; send it with the same query filters and do not parse it. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `page_size` | Maximum number of items requested on one page; defaults to 100 and cannot exceed 500. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `source` | Resource that identifies where evidence originated. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `geography_type` | The kind of place identified by a geography, such as county or state. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `geography_id` | Identifier for a geography; county and state IDs use their corresponding FIPS lengths. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `period_start` / `period_end` | Start and end dates describing the time covered by an observation. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `temporal_grain` | Label for the time unit represented by an observation, such as a year or day. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `NO_RECORDS` | Value state preserving a publisher's literal “No records” result; it does not establish biological absence. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `NO_COUNTY_LINKED_RECORD` | Value state preserving a categorical no-county-linked-record result; it is neither null nor zero. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `MISSING` | Value state that marks a missing value; it is distinct from zero, suppressed, unavailable, and no-record states. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `SUPPRESSED` | Value state identifying a value withheld by the publisher or governed data process. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `UNAVAILABLE` | Availability or value state indicating that the documented result is not published or cannot be supplied; it does not imply zero. | [Typed observation selection availability](public-api-v1-contract.md#typed-observation-selection-availability-api-237) |
| `response_at` | Timestamp for when Atlas constructed the response, not a source update time. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `publisher` timestamp | Timestamp supplied by a publisher when governed; a missing timestamp is not inferred freshness. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `methodology_version` | Version of the transformation or method applied to produce the result. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |

Do not collapse `null`, zero, `MISSING`, `SUPPRESSED`, `UNAVAILABLE`,
`NO_RECORDS`, or `NO_COUNTY_LINKED_RECORD`. Applicability and evidence quality
are separate concepts and are not `value_state` values. See the contract for
the complete enum and its boundaries.

## Copy-ready templates

### Operation summary

`[Verb] [the resource or result] [for the supported scope].`

Example: `Query published county observations.`

### Operation description

```markdown
[One sentence: what the operation returns and for whom.]

### When to use it
[State the task this operation supports and any relevant safety limit once.]

### Key parameters
| Parameter | What to send |
| --- | --- |
| `[name]` | [Meaning, accepted form, and bound.] |

### What you get back
[Describe the response shape and how to interpret important values.]

### Common problems
| Response | Cause and next step |
| --- | --- |
| `[status]` | [Cause; how to correct the request or recover.] |
```

Keep headings that do not apply only when they help the reader. Do not invent
behavior to fill a template.

### Parameter description

`[What the value selects]. [Accepted format or range]. [Requiredness or interaction with another parameter, if relevant].`

### Schema description

`[What this field represents]. [How to interpret its values, nullability, or relationship to other fields].`

### Error description

`[Why this request receives this response]. [What the client can change or do next].`

### Example description

`[What scenario the example illustrates]. [Whether values are synthetic or abbreviated and any interpretation limit].`

## Examples from the current public specification

These examples show how to shorten and organize text without changing contract
meaning. The before text is quoted from the checked-in public OpenAPI artifact.

### 1. Observation operation description

**Before:** “Current published county observations only. Ordered by measure ID,
county FIPS, period start, and observation ID. Opaque page tokens bind to
filters and release. Annual surveillance uses 2023. Separately enabled, reviewed
January 2025 environmental measures use labeled daily periods, carry
environmental_context coverage, and have no supported strata. Environmental
context is descriptive weather, with no causal, disease-risk or ML claim.”

**After:** “Query published county observations using one measure and a bounded
county and time selection. Results are ordered by measure ID, county FIPS,
period start, then observation ID. Page tokens are opaque and bound to the
filters and release.

Current publication includes annual surveillance for 2023 and, when enabled,
reviewed January 2025 environmental measures with labeled daily periods. These
measures include `environmental_context` coverage and have no supported strata.
Environmental context describes weather; it makes no causal, disease-risk, or
machine-learning claim.”

### 2. Observation query rules

**Before:** “A measure and bounded county/time selection are required; no user
sorting or aggregation.”

**After:** “Choose one measure and a bounded county and time selection. Results
use the API's documented order; the query does not sort or aggregate them.”

### 3. Observation no-results interpretation

**Before:** “After successful release/measure/query reads, an initially empty
non-CDC selection returns 200 with meta.availability=UNAVAILABLE and
reason=NO_PUBLISHED_RESULTS. This does not establish source coverage or
biological absence.”

**After:** “If a successful initial non-CDC query finds no published results,
the API returns HTTP 200 with `meta.availability` set to `UNAVAILABLE` and
`reason` set to `NO_PUBLISHED_RESULTS`. This describes publication for the
selection; it does not establish source coverage or biological absence.”

### 4. HTTP 400 error description

**Before:** “INVALID_REQUEST, UNSUPPORTED_FILTER, UNSUPPORTED_STRATIFICATION,
or QUERY_TOO_BROAD; application/problem+json”

**After:** “The query is invalid, uses an unsupported filter or stratification,
or exceeds the result limit. Check the parameter requirements and narrow the
selection if needed. The response uses RFC 9457 Problem Details.”

### 5. Page token parameter

**Before:** “Opaque next_page_token; retain query filters and release.”

**After:** “Send the opaque token from `meta.next_page_token` to request the
next page. Keep the other query filters unchanged; do not parse the token.
Tokens are tied to the release used by the query.”

### 6. Empty continuation page example

**Before:** “Empty continuation page; not an unavailable selection”

**After:** “An exhausted continuation page has no more items. Its availability
remains `AVAILABLE`; it does not mean the original selection was unavailable.”

## Maintainer review

Before publishing rewrites, check that each term matches the linked contract,
that each applicable safety limit appears once per operation, and that no
sentence turns missing data into zero, absence, diagnosis, or an individual
risk estimate. A product owner must approve this guide before dependent
documentation rewrites begin.
