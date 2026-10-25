# API writing style and shared vocabulary

This guide sets the rules for every sentence in the public API reference: the
OpenAPI `info` block, tags, operation summaries and descriptions, parameters,
schemas, error responses, and examples. Its vocabulary table is the single
source other documents reuse. The guide governs wording only. It does not change
routes, status codes, response shapes, data, or interpretation limits. Where the
[public API V1 contract](public-api-v1-contract.md) defines a term, the contract
wins and this guide must not contradict it.

Related: [OpenAPI quality gate](openapi-quality.md) and the
[public API guide](public-api-guide.md).

## Audience

Write for three readers, in this order:

1. **Developers** who know HTTP and JSON but do not know Atlas.
2. **Analysts and public-health practitioners** who read the reference to learn
   what a number does and does not support.
3. **Generated-client and AI-agent users** who copy descriptions into tooltips and
   prompts, so each description must make sense on its own.

Assume no knowledge of Atlas internals, ticket numbers, or warehouse names.

## Tone

- Be direct, calm, and precise. Sound like a careful colleague, not a legal notice.
- Say what the reader can do first. Say limits second, and say each limit once.
- Prefer plain words. Use a technical term only when it names a real contract
  element (`release_id`, `page_token`, RFC 9457), and define it at first use.
- Do not market, hedge, or apologize. Do not promise behavior the contract does
  not state.

## Sentence rules

1. **Lead with the reader's action.** Start with a verb such as `Query`, `List`,
   `Get`, or `Check`, or with `You can`. Never write `the consumer shall`.
2. **Use active voice.** Write "The API returns 400", not "400 is returned".
3. **Keep sentences under about 25 words** and paragraphs under about four
   sentences.
4. **Use one idea per bullet.**
5. **Define a term at first use.** Link to the [vocabulary](#shared-vocabulary)
   or give a short definition. Do not reuse a term with a second meaning.
6. **State what a thing is before what it is not.** Do not write chains of
   "this endpoint is not... and does not... and never...". One limit, one sentence.
7. **Say it once.** State each safety limit once per operation, using the
   [approved wording](#approved-safety-wording) or a link to it. Put shared limits
   in `info.description`, not in every operation.
8. **Name things by what they are.** Describe behavior and data instead of
   internal mechanisms, pipeline stages, or ticket history.
9. **Keep public text timeless.** Do not include issue or ticket numbers, ADR
   numbers, internal repository names, or the dates something was tested.
   Do write dates that are part of the data, such as `2025-12-31`.
10. **Keep exact values exact.** Preserve field names, enum values, status codes,
    and numbers in code font. Do not paraphrase an enum value.

## Approved safety wording

Three limits recur across the API. Write them in exactly these words, and place
each one at most once per operation (or once in `info.description` with a link).
The meaning comes from the contract and from `info.description`; do not weaken,
extend, or merge them.

| Limit | Approved wording |
| --- | --- |
| Not a diagnosis | Atlas results are not a diagnosis. |
| Not an individual risk estimate | Atlas results are not an estimate of an individual's risk. |
| Response time is not freshness | The response time shows when Atlas built the response. It does not show when the publisher last updated the data. |

Shorter forms are allowed in a schema field when a link or the surrounding
operation already states the full wording, for example "Descriptive only; not a
diagnosis."

Related interpretation rules that must also be preserved wherever they apply, and
that are written through the vocabulary instead of repeated: missing data is not
zero, and `NO_RECORDS` or `UNAVAILABLE` never establishes biological absence.

## Banned and replace list

Do not use the left-hand terms in public text. Use the right-hand wording.

| Avoid | Write instead |
| --- | --- |
| pre-execution logical-result ceiling | result limit (state the number, for example 10,000, and that a request over the limit is rejected, not truncated) |
| logical-result ceiling | result limit |
| admitted Data delivery | describe what the data is, for example "published surveillance context" |
| persisted (of a result) | published |
| persisted (of storage) | stored |
| typed 200 | describe the HTTP 200 body and its `availability` value |
| typed availability | the `availability` field |
| retained / existing / legacy (as the whole explanation) | describe the behavior, for example "returns the same fields as before" |
| Fumadocs, `/docs`, and `/redoc` consume... | remove from `info`; tooling notes belong in contributor docs |
| consumer-safe | suitable for public use, or say the specific reviewed property |
| producer-native / source-native | as supplied by the publisher |
| fail closed | return an error instead of partial data (name the status code) |
| no-store | the response must not be cached |
| exact Data record admission | the record is published for that county |
| bundle, slice, projector, view (warehouse sense) | describe the data, not the pipeline |
| resource503 / typed 503 | HTTP 503 |
| ticket or issue numbers, `API #nnn`, `ADR nnnn` | remove; link to a public document if needed |
| "this endpoint is not..." chains | one positive sentence, one limit sentence |

Do not remove technical names that identify a real contract element: HTTP status
codes, RFC 9457, `release_id`, `page_token`, `Retry-After`, `ETag`.

## Shared vocabulary

One plain-language definition per term. Reuse these definitions word for word
when a description needs them. The last column cites the governed definition in
[`public-api-v1-contract.md`](public-api-v1-contract.md) where one exists.

| Term | Plain-language definition | Contract source |
| --- | --- | --- |
| `release_id` | Identifies the published data release a result came from. Results from different releases are not directly comparable. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `value_state` | Says how to read an observation's value: `OBSERVED`, `ZERO`, `MISSING`, `SUPPRESSED`, `UNAVAILABLE`, `NO_COUNTY_LINKED_RECORD`, or `NO_RECORDS`. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `OBSERVED` | A nonzero value the publisher reported. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `ZERO` | A reported numeric zero. It is different from a missing value. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `MISSING` | No value was reported. It is not zero. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `SUPPRESSED` | The value exists but is withheld. It is not zero. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `UNAVAILABLE` | Atlas has no published result for this selection. It does not mean zero or absence. | [Typed observation selection availability](public-api-v1-contract.md#typed-observation-selection-availability-api-237) |
| `NO_RECORDS` | The publisher's literal "No records" result. It does not establish biological absence. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `NO_COUNTY_LINKED_RECORD` | A governed status meaning no record is linked to the county. It is neither null nor zero. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `vintage` | The reference year or period of a source dataset, such as ACS 2018-2022 or the 2023 RUCC codebook. A vintage is not an annual value. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics); [County socioeconomic and rurality context](public-api-v1-contract.md#county-socioeconomic-and-rurality-context-api-212) |
| FIPS | The numeric code for a place: five digits for a county, two for a state. Keep leading zeros. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `geography_type` | The kind of place: `county` or `state` where supported. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `geography_id` | The FIPS code that identifies one place. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `indicator` | A public-health concept that groups related measures. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `measure` | One variable you can query, for example a case count. A measure's entry does not promise that every county and period has data. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `source` | The organization and dataset a value came from, identified by `source_id`. | [Source and methodology resources](public-api-v1-contract.md#source-and-methodology-resources-55) |
| `methodology` | The method Atlas used to produce or interpret a value. It has an ID and a version. | [Source and methodology resources](public-api-v1-contract.md#source-and-methodology-resources-55) |
| `methodology_version` | The version of the transformation applied to an observation. It is separate from the version of the methodology resource. | [Source and methodology resources](public-api-v1-contract.md#source-and-methodology-resources-55) |
| `provenance` | Where a result came from: source, retrieval time, methodology and version, geography, and release. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `limitations` | Factual notes on what a result does not show or where its evidence is bounded. Read them with the value. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `availability` | Whether Atlas has published results for the selection: `AVAILABLE` or `UNAVAILABLE`. It does not describe biological presence. | [Typed observation selection availability](public-api-v1-contract.md#typed-observation-selection-availability-api-237) |
| `page_size` | Items per page. The default is 100 and the maximum is 500. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| `page_token` | An opaque value you send to get the next page. Send it unchanged, with the same filters. Do not parse it. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics); [Public read protection](public-api-v1-contract.md#public-read-protection-56) |
| `next_page_token` | The token in `meta` for the next page. `null` means there are no more pages. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| result limit | The maximum number of results one query may select (default 10,000). Over-broad queries are rejected with 400 `QUERY_TOO_BROAD`, never truncated. | [Bounds and access](public-api-v1-contract.md#bounds-and-access) |
| `period_start` / `period_end` | The first and last dates an observation covers. They can be `null` for a categorical vintage. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| response time | When Atlas built this response. It is not when the publisher last updated the data. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| Problem Details | The RFC 9457 error body (`application/problem+json`) with a stable `code` and a `request_id` for support. | [Public read protection](public-api-v1-contract.md#public-read-protection-56) |
| `Retry-After` | Seconds to wait before you retry a 429 or 503 response. | [Public read protection](public-api-v1-contract.md#public-read-protection-56) |
| surveillance floor | A reported count that is a minimum, not a complete count. Floors are not diagnoses or individual risk estimates. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |
| applicability | Whether a measure applies to a place, period, and stratum. It is separate from `value_state`. | [Resources and semantics](public-api-v1-contract.md#resources-and-semantics) |

Do not merge `null`, zero, `MISSING`, `SUPPRESSED`, `UNAVAILABLE`, `NO_RECORDS`,
or `NO_COUNTY_LINKED_RECORD`. Applicability and evidence completeness are separate
ideas and are not `value_state` values. For the full list and its boundaries, see
the contract.

## Templates

Copy these and fill in the brackets. Delete a heading that has nothing to say.
Do not invent behavior to fill a template.

### Operation summary

Start with a verb. Name the resource and the scope. Use fewer than 8 words and no
trailing period.

```text
[Verb] [resource] [scope]
```

Examples: `Query county observations`, `List measures`, `Get a source`.

### Operation description

```markdown
[One sentence: what the operation returns, for whom, and the scope.]

### When to use it
[The task this operation supports, in one or two sentences. Link to the
`info.description` section for shared limits. Add an operation-specific limit
here once, if one exists.]

### Key parameters
| Parameter | What to send |
| --- | --- |
| `[name]` | [What it selects. Accepted form and bounds.] |

### What you get back
[Response shape in one sentence. Then the fields that need interpretation, one
bullet each.]

### Common problems
| Response | Cause and what to do |
| --- | --- |
| `[status] [CODE]` | [Why it happens. What the client changes.] |
```

### Parameter description

```text
[What the value selects.] [Accepted form, range, or default.] [Interaction with another parameter, only if it matters.]
```

Example: `County FIPS code. Use five digits and keep leading zeros. Repeat the parameter for up to 500 counties.`

### Schema description

```text
[What this object or field is.] [How to read its values, including null.]
```

Example: `Source of the value. Read it with methodology and limitations.`

### Error description

```text
[Why the request gets this response.] [What the client can change or do next.]
```

Example: `The request is invalid or too broad. Fix the parameters or narrow the selection, then retry.`

### Example

An example needs a name (`summary`) and a one-sentence `description` that says
which scenario it shows and, when values are synthetic or shortened, says so.

```text
summary: [Scenario in 3-6 words]
description: [What this scenario shows. Say if values are synthetic or abbreviated.]
```

Example: `summary: Initial query, no published results` /
`description: Shows HTTP 200 with an empty data array and UNAVAILABLE availability.`

## Before and after rewrites

The "before" text is quoted from the live public specification
(`https://api.onehealthatlas.org/openapi.json`). The "after" text keeps the same
meaning and applies this guide. These are illustrations; later stories apply
them to the spec.

### 1. `GET /v1/observations` description

**Before** (about 205 words in a single block, abridged here to its opening and
its no-results rule):

> Current published county observations only. Ordered by measure ID, county FIPS,
> period start, and observation ID. Opaque page tokens bind to filters and
> release. [...] Surveillance context is optional and requires an admitted Data
> delivery; normalized_state preserves Data semantics and the publisher value
> remains value. [...] A measure and bounded county/time selection are required;
> no user sorting or aggregation. After successful release/measure/query reads, an
> initially empty non-CDC selection returns 200 with
> meta.availability=UNAVAILABLE and reason=NO_PUBLISHED_RESULTS. This does not
> establish source coverage or biological absence.

**After:**

> Query published county observations for one measure, a set of counties, and a
> year or date range.
>
> ### When to use it
> Use this to read values with their `value_state`, source, methodology, and
> limitations. Results are in a fixed order: measure ID, county FIPS, period
> start, then observation ID. You cannot sort or aggregate.
>
> ### What you get back
> A page of observations, with `next_page_token` when more pages exist. If an
> initial query finds no published results, the API returns HTTP 200 with
> `meta.availability` set to `UNAVAILABLE` and `reason` set to
> `NO_PUBLISHED_RESULTS`. That says nothing about source coverage or biological
> absence.
>
> ### Common problems
> See the 400 response for invalid or over-broad queries.

The detail removed from the opening (environmental context, CDC status, county
context vintages) moves to the parameter descriptions and the `info` interpretation
section, where each rule appears once.

### 2. 400 error description

Used on every operation.

**Before:**

> INVALID_REQUEST, UNSUPPORTED_FILTER, UNSUPPORTED_STRATIFICATION, or
> QUERY_TOO_BROAD; application/problem+json

**After:**

> The request is invalid, uses an unsupported filter or stratification, or selects
> more than the result limit. The response is RFC 9457 Problem Details; its `code`
> is `INVALID_REQUEST`, `UNSUPPORTED_FILTER`, `UNSUPPORTED_STRATIFICATION`, or
> `QUERY_TOO_BROAD`. Fix the parameters or narrow the selection, then retry.

### 3. `page_token` parameter

**Before:**

> Opaque next_page_token; retain query filters and release.

**After:**

> Send the `next_page_token` from the previous page to get the next page. Keep the
> other filters unchanged. Do not parse the token.

### 4. Pagination paragraph in a discovery operation

**Before** (`GET /v1/measures`):

> Current-release discovery with optional documented filters. page_size defaults to
> 100 (maximum 500); reuse opaque page_token with the same filters and release. A
> null next_page_token ends traversal. Cache-Control: public, max-age=60,
> must-revalidate.

**After:**

> List measures in the current release. You can filter by `indicator_id`,
> `measure_id`, or `geography_type`.
>
> ### Key parameters
> | Parameter | What to send |
> | --- | --- |
> | `page_size` | Items per page. Default 100, maximum 500. |
> | `page_token` | The `next_page_token` from the previous page, with the same filters. |
>
> A `null` `next_page_token` means there are no more pages. Responses can be
> cached for 60 seconds.

### 5. `info.description`: result limit and contract consumers

**Before:**

> The default pre-execution logical-result ceiling is 10,000, not truncation.
> [...] Fumadocs, `/docs`, and `/redoc` consume the canonical public `openapi.json`
> at `/openapi.json`.

**After:**

> A query may select up to 10,000 results. A broader query is rejected with 400
> `QUERY_TOO_BROAD`; the API never truncates results.
>
> (Remove the second sentence. It describes documentation tooling, not the API.)

### 6. `Observation.value_state` schema description

**Before:**

> OBSERVED is nonzero; ZERO is numeric zero; MISSING/SUPPRESSED/UNAVAILABLE require
> null; NO_COUNTY_LINKED_RECORD retains the literal no_county_linked_record;
> NO_RECORDS retains CDC's No records. Neither establishes biological absence.

**After:**

> How to read `value`. `OBSERVED` is a nonzero value and `ZERO` is a reported zero.
> `MISSING`, `SUPPRESSED`, and `UNAVAILABLE` mean `value` is `null`. `NO_RECORDS`
> and `NO_COUNTY_LINKED_RECORD` keep the publisher's wording as the value. Neither
> establishes biological absence.

### 7. Availability operation description and 200 response

**Before** (operation description, abridged):

> Additive county-only operation. Confirms membership and release from the
> governed county directory before returning
> UNAVAILABLE/CANONICAL_GEOGRAPHY_UNPUBLISHED with null data. [...] The directory
> uses the existing bounded snapshot cache, checked against a fresh current-release
> lookup; a mismatch remains 503.

200 response: `Confirmed current county identity; canonical publication is pending.`

**After:**

> Check whether a county has published geography data. Today the API confirms the
> county exists and returns HTTP 200 with `availability` set to `UNAVAILABLE`,
> `reason` set to `CANONICAL_GEOGRAPHY_UNPUBLISHED`, and `data` set to `null`. This
> describes identity data only. The API returns no geometry.
>
> 200 response: `The county exists. Its canonical geography data is not published yet.`

The cache and directory mechanics are implementation detail and leave the public text.

### 8. Schema wording with "persisted" (`Tier1CountyPriorityAvailability.availability`)

**Before:**

> Whether a persisted Tier 1 result exists for this supported current geography.

**After:**

> Whether a published Tier 1 result exists for this county.

## Maintainer checklist

Before a rewrite story merges, check that:

- Each term matches the vocabulary above and the contract section it cites.
- Each applicable safety limit appears once per operation, in the approved wording.
- No sentence turns missing data into zero, absence, a diagnosis, or an individual
  risk estimate.
- No banned term, ticket number, or test date appears in public text.
- Any new term is added to the vocabulary table, with its contract source, in the
  same change.

Changing this guide's vocabulary or safety wording changes a public
interpretation. Product-owner approval is required before dependent rewrite
stories start, and again for later changes to the approved safety wording.
