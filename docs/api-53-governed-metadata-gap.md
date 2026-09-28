# API #53 governed discovery prerequisite

Status: blocked on a Data-owned publication contract. This note records the
read-only source audit; it does not authorize publication or change API #52.

The current API runtime reads `PRESENTATION.CURRENT_RELEASE_V`,
`CURRENT_SOURCE_METADATA_V`, and `CURRENT_COUNTY_ATLAS_V`. Data migration V072
grants those three views to the API runtime role. None supplies indicator or
measure definitions. V071 stores release-local `SEMANTIC_INDICATORS` and
`SEMANTIC_MEASURES`, but V072 does not publish them to the API role. Querying
those tables directly would bypass the reviewed consumer boundary.

Data #194's `atlas-semantic-consumer-v1` is a storage-neutral, observation-level
projection, explicitly without HTTP publication or a runtime view. Its indicator
object contains only `id`. It does provide reviewed measure definition fields,
but only as part of validated lineage for a particular observation. The fixtures
are synthetic and do not authorize a public list. In contrast, the authoritative
API #52 `Indicator` response requires a label, definition, measure IDs, and a
semantic version. Its `Measure` response also requires denominator, geography
types, temporal grains, methodology ID, source IDs, and public value states.
These cannot be filled truthfully from the three current API views. V071's
release-local measure rows also lack a denominator and semantic version.

Before #53 can return 200, Data ownership needs a reviewed, bounded, current
release discovery projection or service that joins publication authorization to
semantic definitions and exposes exactly the public-safe indicator/measure
fields, source and methodology references, semantic/metadata revisions, release
identity, and supported geography/time/strata. It must specify how an indicator's
label, definition, and semantic version are governed, and whether a domain or
availability filter has approved meaning. The API runtime role needs explicit
least-privilege access to that projection. The API can then add a thin repository
adapter, application service, caching, pagination, and HTTP tests without
duplicating the Data registry.

Relevant Data ownership: #190 (domain identity), #191 (metadata), #194
(consumer projection), and #195 (governance) are closed contract stories. No
open Data story for this runtime publication prerequisite was identified in the
read-only issue search. API #53 should remain open until the prerequisite is
reviewed and delivered; #54 observation mapping does not substitute for it.
