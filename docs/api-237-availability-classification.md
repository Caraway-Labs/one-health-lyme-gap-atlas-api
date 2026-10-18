# API #237: availability classification before implementation

Baseline: `af70c64077aa94e89622b485cf3a2b0eecf47007`. Implementation is authorized;
merge/deployment are not. This inventory classifies causes, not exception text:
the same sanitized `SERVICE_UNAVAILABLE` can represent multiple failures.

| Endpoint/path family | Cause and evidence | Classification | Request successfully determined authoritative state? | Current code/status | Outcome in this increment; client impact |
|---|---|---|---|---|---|
| `/v1/observations`, both CDC measures | Producer rejects unknown-source coverage, absent exact record membership, wrong release/source/metadata or inconsistent proof. Pinned Data result exposes admitted observations only. | UNDETERMINED | No safe unavailable discriminant reaches API | 503 SERVICE_UNAVAILABLE | Keep 503; no scientific inference, empty200 or fallback |
| `/v1/observations`, CDC connection/query/timeout/authentication | Dependency failure before validated result | OPERATIONAL_FAILURE | No | 503 SERVICE_UNAVAILABLE | Keep 503 |
| `/v1/observations`, ordinary non-CDC bounded query | Valid release and published measure, successful query returns no rows on initial page | KNOWN_UNAVAILABLE (published selection only) | Yes: no published results for the exact selection; not biological absence or county validation | Existing empty200 | Add meta availability UNAVAILABLE/reason NO_PUBLISHED_RESULTS, release and caveats; data remains[] |
| `/v1/observations`, empty continuation | Exhausted page does not prove initial selection empty | Not an unavailable determination | Yes: pagination exhausted | Existing empty200 | AVAILABLE/null reason; retain exhausted token semantics |
| `/v1/observations`, SVI/insurance/population/RUCC missing initial selection | Existing explicit no admitted context error; do not reinterpret admission | UNDETERMINED | No authorized unavailable classifier | 404 RESOURCE_NOT_FOUND | Keep 404 |
| `/v1/geographies/{type}/{id}` | Local pending canonical identity-publication capability, schema has no geometry field; current validator checks shape only | KNOWN_UNAVAILABLE capability, UNDETERMINED identity membership | Capability known; identity not established by shape | 503 CANONICAL_DATA_UNAVAILABLE | Preserve legacy route; additive county availability validates current identity/release before typed200, no fabricated data |
| Canonical geography dependency failure, missing/duplicate/malformed current identity authority | Cannot establish membership/current release | OPERATIONAL_FAILURE or UNDETERMINED | No | 503 SERVICE_UNAVAILABLE / existing typed404 for confirmed absence | Keep real errors; no fabricated geography or analysis geometry |
| `/v1/intelligence/items`, `/v1/intelligence/sources`, `/v1/intelligence/feed` disabled | Config explicitly disables service; not evidence that a scientific query was evaluated | KNOWN_UNAVAILABLE capability | Known disabled configuration only | 503 SERVICE_UNAVAILABLE | Retain current internal intelligence contracts; no unrelated envelope change. Separate capability contract review required |
| Same intelligence routes enabled, projection/authority errors | Repository, source admission, parsing, consistency or release failure | OPERATIONAL_FAILURE / UNDETERMINED | No | 503 SERVICE_UNAVAILABLE | Keep503; an already successful empty projection stays200 |
| `/v1/counties/{fips}/tier1-surveillance-priority/availability` | Current geography validated, active projection checked, no model result | KNOWN_UNAVAILABLE | Yes | Existing200 NO_PUBLISHED_MODEL | Unchanged reference pattern |
| Tier1 direct result, active batch absent/inaccessible, malformed or duplicate results | Failure/invalid authority distinct from confirmed county no-model result | OPERATIONAL_FAILURE / UNDETERMINED | No | 503 SERVICE_UNAVAILABLE (confirmed absent result404) | Unchanged |
| Atlas metadata/scores/counties/geometry/ranking; public catalogs, sources, methodologies | Snowflake/auth/query or malformed release/source data | OPERATIONAL_FAILURE / UNDETERMINED | No | AtlasDataUnavailableError ->503 SERVICE_UNAVAILABLE | Unchanged global handler and admission |
| `/health/ready` | Required dependency, chat wiring or unsafe multiworker feedback topology | OPERATIONAL_FAILURE | No readiness | 503 SERVICE_UNAVAILABLE | Unchanged; never report healthy200 |
| `/v1/me/*`, feedback/profile/privacy; auth dependency | Unconfigured auth, storage/admin/repository failure | OPERATIONAL_FAILURE | No authorized successful operation | 503 SERVICE_UNAVAILABLE (401/403 remain distinct) | Unchanged |
| County/state PDF exports | Renderer timeout/unavailable or mismatched provenance | OPERATIONAL_FAILURE / UNDETERMINED | No valid report produced | 503 SERVICE_UNAVAILABLE | Unchanged; resource limit413 retained |
| `/v1/assistant/mixed`, `/v1/knowledge-graph/chat` | Disabled/unwired capability, evidence or provider unavailable, capacity | Known disabled capability or OPERATIONAL_FAILURE/UNDETERMINED depending cause | No verified scientific availability determination | Existing503 typed assistant response/Retry-After | Unchanged internal AI contract; no blanket HTTP translation |
| Invalid filters/strata/IDs, unsupported cumulative date/year | Invalid query, not unavailable science | Client error | No valid request | Existing400/422; absent resource404 | Unchanged |
| Abuse limits / unexpected exceptions | Rate/concurrency protection / unhandled bug | Operational protection/failure | No | 429 /500 | Unchanged |

## CDC scientific gate — blocked portion

The exact installed Data dependency is commit
`2476bfec3b89b513adf9ce2348fe94be44abf915` (pyproject/uv.lock).
`cdc_consumer_runtime._inputs` explicitly rejects
`coverage_state == UNKNOWN_SOURCE_COVERAGE` together with source/release/record
authority mismatches. `CDCBatchResultV1` has only measure, release, counties,
admitted observations and contract version. It has no validated unavailable
result. `_project_batch` requires one exact authoritative row per requested
county and complete shared proof; any violation raises `CDCConsumerUnavailable`.
The API wraps producer errors as sanitized `AtlasDataUnavailableError`.

Therefore county02122's historical503 does not establish a consumer-authorized
UNKNOWN_SOURCE_COVERAGE classification. No such200 path or synthetic fixture
claim is implemented. Data must first publish a reviewed, versioned consumer-safe
availability discriminant binding measure/county/period/release and authoritative
reason, with source/admission validation complete and operational failures
distinct. It must define partial-page/mixed-county and continuation behavior.
No Data files, pins, admission rules or private authority are changed here.
