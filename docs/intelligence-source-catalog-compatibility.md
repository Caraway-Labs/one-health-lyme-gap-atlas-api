# Published intelligence source catalog

The metadata-only `/v1/intelligence/feed` always reads the existing V2
presentation. `/v1/intelligence/sources` must include its represented sources
even when the legacy `/items` projection setting is V1.

The source catalog returns one row per source_id across both existing V1 and V2
published projections. That row is the highest registry_version, with the
organization and reviewed trust classification from that version. When several
rows share that version, the catalog keeps the alphabetically first organization,
then the alphabetically first reviewed trust classification. V1 sources remain
represented. It does not enumerate the
private registry, admit a source, or expose publisher native trees.
Operational state is read from `PRESENTATION.INTELLIGENCE_SOURCE_HEALTH_V1`
in one bounded query and cached for five minutes. A failed health read is
cached for one minute. Several health rows for one source_id still produce one
catalog row; the greatest `last_completed_at` wins, and equal times prefer failing,
then stale, then healthy. That row supplies `operational_state`,
`last_completed_at`, and `last_error_code`. A missing health view or any failed
health read leaves the catalog at HTTP 200 with every source `unavailable` and
diagnostic `SOURCE_HEALTH_NOT_PUBLISHED`.
Both feed views must be available to the existing API reader; an unavailable feed
view returns the existing sanitized 503, never a fallback or partial list.

The response includes source identity, registry version, organization, reviewed
trust classification, parameter binding, the result ceiling, query timeout,
deterministic source/version ordering, and state-bound pagination. The response
must not be cached.
A publication change restarts pagination. Source catalog completeness
remains false because registered sources without published items are excluded.

This supports Web #816 / PR #822 without duplicating its filters or generating a
new client contract. New-source delivery still requires governed capture,
accepted records, actual service retrieval and rendered publisher links.

Approval to merge this API PR authorizes the repository's existing main deployment
after its quality and procedural peer-review gates. It creates no credentials,
grants, data migration, ingestion or Data scheduler update. Rollback is the
existing main-revert deployment. Before merge, verify both published views under
the intended API identity; after deployment verify a nonempty source list and
that each returned new source has a corresponding successful `/feed` response.
