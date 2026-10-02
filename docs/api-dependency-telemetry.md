# API dependency spans (#160)

`atlas.dependency.<system>.<operation>` CLIENT spans time actual synchronous SDK
boundaries beneath the current request/stage. Fields are `atlas.dependency.system`,
`operation`, `outcome` (success/failure), and `failure_class`
(none, timeout, upstream_http, dependency_error, cancelled). Unknown system or
operation collapses to `other`. Span timing is elapsed wall clock per call.
No SQL/Cypher, query tags/IDs, parameters, bodies, returned rows, prompts,
credentials, hosts, paths, user identity or exception messages are copied.

Coverage:

- Snowflake legacy snapshot/readiness, public metadata/observations/provenance,
  and feedback procedure adapters delegate to the shared connection constructor.
  Connect, execute, fetchone/fetchall, explicit close, and context teardown are
  timed independently. Cursor construction and context entry are local SDK work
  and have no dependency span. Teardown includes the SDK's
  commit/rollback/close work. There is no span covering the connection lifetime.
- Supabase profile, privacy and auth-admin requests include network response
  reading and HTTP status checking. JWKS spans cover actual network refresh,
  including cache misses, rather than cached JWT parsing/key lookup.
- Neo4j retrieval and outbound OpenAI embedding/generation are used only by
  Ask Atlas production routes and already have their stage spans. Ask Atlas
  Snowflake operations retain `knowledge_chat.snowflake.*`; this change does
  not wrap or duplicate them. The asynchronous startup graph census is not a
  request dependency. PR158 preserves the owning service/stage nesting contract.

The FastAPI SERVER span is total HTTP duration. Ask Atlas service/stage duration
and their nested Snowflake/provider spans overlap. Never sum nested spans as
request wall time. Compare individually or compute exclusive time/interval union;
untraced application/serialization/transport time remains part of the root.
These intervals are not server query execution statistics and can include SDK
retries/network work. Caches may legitimately produce no dependency spans.

Original SDK arguments, returned data, context suppression, error objects and
close behavior are preserved. Optional tracer start/attribute/end errors do not
affect operations. Existing provider/exporter, sampling and deployment are reused.
