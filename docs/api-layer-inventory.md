# API layer inventory for Epic #96

Status: Story #97 inventory, 2026-09-26. This document describes the current
API at `0c78197` and the shared-python source at
`83ccbe76047185f6aacaff551afd8b03d88aa5cf` (source version `1.0.0`).
The API currently pins shared-python `v0.1.7`; no `v1.0.0` tag is assumed.

## Boundary and component map

The browser calls this FastAPI service. `app.py` is the public REST adapter:
request parsing, status/error mapping, response models, headers, CORS and rate
limits. `AtlasService` in `service.py` is an in-process application layer over
the `AtlasRepository` protocol. `SnowflakeAtlasRepository` in `repository.py`
loads the governed release snapshot; its SQL and connector use remain API
runtime concerns. `models.py` owns the public OpenAPI DTOs. The report,
feedback, privacy and chat modules each already contain their own service and
store/retriever seams. There is no reason to create another deployed service.

| Path | Current route and application behavior | Infrastructure and ownership |
| --- | --- | --- |
| `/v1/atlas/metadata`, `/geometry` | `app.py` maps HTTP headers, ETag, 304 and 404; `AtlasService` selects release and builds GeoJSON. | `SnowflakeAtlasRepository` loads the snapshot. Geometry shape, CRS and cache policy stay API-owned. |
| `/v1/atlas/scores`, `/v1/counties/{fips}` | `app.py` parses `ScoreSettings`, maps 404 and cache headers; `AtlasService` selects a snapshot, scores, sorts and builds public summaries/details. | Shared `CountyInputs`, `ScoreSettings`, `score_county`, `priority_label`, `score_color` already supply portable logic. `CountyScoreSummary`, `CountyDetail`, `ScoreCollection`, `AtlasMetadata` remain API OpenAPI DTOs. |
| `/v1/atlas/ranking.csv` | `AtlasService` filters scored results and writes CSV; `app.py` sets content type and filename. | Search/filter and CSV column order are API product behavior, not a common domain contract. |
| County/state PDF routes | `app.py` handles template keys, conditional requests, ETag, file response and renderer errors. `ReportService` assembles report data. | `reports/` owns template registry, Typst rendering, resource limits and artifact cache. Preserve frozen template versions and report provenance. |
| `/v1/feedback` | `app.py` maps request/auth, privacy-safe errors and response; `FeedbackService` handles idempotency. | `SnowflakeFeedbackStore` owns SQL/write procedure, protected contact data and account linkage. |
| `/v1/knowledge-graph/chat` | `app.py` handles request, rate/policy errors and response; `KnowledgeChatService` handles retrieval, evidence validation and answer orchestration. | `Neo4jRetriever`, `OpenAIAnswerer`, Snowflake budget and corpus-provenance stores remain API runtime integrations. Graph evidence and fail-closed rules are distinct from score-domain logic. |
| `/v1/me/profile`, `/v1/me/privacy-requests*` | `app.py` verifies bearer auth, maps private errors and no-store headers. `PrivacyRequestService` executes the data-rights workflow. | Supabase token/admin/profile/privacy stores, Snowflake feedback linkage and account isolation are API-specific security behavior. |
| `/health/*` | `app.py` reports process/live readiness. | Snowflake readiness and optional chat wiring are deployment policy. |

Environmental context and ML exposure currently travel as fields in the
governed county/release snapshot and public score/detail/report responses;
there is no independent environment or ML route in this package. Their value,
method/version and limitation meanings must remain tied to source provenance,
not inferred from superficially similar portable fields.

## Actual duplication and placement

`lyme_gap_atlas_shared.domain` exports `CountyInputs`, `Provenance`, `Score`,
`ScoreSettings`, `normalize_county_fips`, `priority_label`, `score_color`, and
`score_county`. `service.py` already uses the scoring contracts and functions
through root imports; `app.py` and `reports/service.py` use `ScoreSettings`, and
`models.py` embeds shared `Score`. These are existing reuse, not duplication.
The new `domain` namespace makes the portable dependency explicit.

County FIPS is validated in `app.py`'s path pattern and `CountyRecord.fips`;
feedback context has separate FIPS patterns, and report paths have their own
input checks. This overlaps the strict five-ASCII-digit rule in shared
`normalize_county_fips`. The route's pattern is also public OpenAPI/422
behavior; replacing it outright could change the contract. A service-level
normalization call can make the invariant explicit while retaining the HTTP
pattern. Feedback and report validation must be assessed independently before
reuse because their input/error semantics differ.

`CountyScoreSummary`, `CountyDetail`, `ScoreCollection`, `AtlasMetadata`,
`ProblemDetails`, account/feedback/chat DTOs and `ReportProvenance` are not
duplicates of shared `Score` or `Provenance`: they encode release metadata,
source/freshness, public response, privacy or evidence semantics. Keep them
API-owned. `AtlasService._matches` and CSV generation are API product rules;
Typst rendering, ETags, cache TTL, Snowflake SQL and connection settings are
runtime policies. Similar-looking validation or DTO fields alone do not
justify extraction to shared-python.

The current API imports legacy shared `settings.SnowflakeSettings` and
`snowflake.connect` in `config.py`, `repository.py`, `feedback.py` and
`knowledge_chat.py`. The shared 1.0.0 package retains these shims behind its
`snowflake` extra. API-owned settings/connectivity migration is separate debt;
moving SQL or credentials to shared-python would reverse the accepted boundary.

## Prioritized migration map

1. **Story #98, bounded score/county path:** upgrade the API's shared pin to
   immutable commit `83ccbe...` with explicit `snowflake` and `observability`
   extras; import portable contracts/logic from `domain`. Keep the existing
   `AtlasService` repository injection and public DTO mapping. Make county FIPS
   normalization an in-process service invariant without changing the path
   pattern or 404/422 behavior. If a route still mixes reusable orchestration
   with HTTP, move only that orchestration into this service. Prove the service
   directly with a fake repository and preserve route tests.
2. **Story #99:** lock down affected success/error payloads, provenance,
   freshness, cache/authorization and generated OpenAPI. Verify shared package
   version, export identity and required extras in the installed environment.
3. **Future separately scoped work:** evaluate API-owned replacement of legacy
   shared Snowflake settings/connectivity after consumer coordination. Consider
   broader route extraction only where tests show a real application seam.

Do not refactor chat/evidence, feedback, account/privacy, PDF/Typst, geometry
delivery or Snowflake query construction in #98. Those paths have distinct
security, privacy, immutable-template, geospatial and freshness constraints and
already have service seams. Do not change the browser/API boundary, create a
worker or network hop, move persistence into shared-python, or invent a shared
model for API-specific response semantics.

## Contract and verification risks

`openapi.json` is the public contract. A changed FastAPI path annotation,
`ScoreSettings` dependency signature, response DTO, exception mapping, cache
header, ETag serialization or release selection could alter observable REST
behavior. Score output includes methodology and release identifiers; county
detail carries the full release metadata with sources, timestamps and
limitations. Tests must compare these, plus invalid FIPS and missing
county/release cases. Keep Snowflake key-pair/least-privilege configuration,
Neo4j evidence gates, Supabase authorization, feedback privacy and rate limits
in API runtime code. The `quality-deploy.yml` gate includes uv, ruff, mypy,
pytest, OpenAPI export/diff, Docker build, production-Typst regression and
secret scanning. Independent quality runs are not serialized. Production
promotion stays on explicit workflow dispatch; see
`docs/ci-and-production-deploy.md`.

Relevant decisions: workspace ADRs 0002 and 0003, API ADR 0016, shared-python
ADR 0001, and the workspace technology/governance baseline.
