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
private registry, admit a source, infer health, or expose publisher native trees.
Both views must be available to the existing API reader; an unavailable view
fails closed with the existing sanitized 503, never a fallback or partial list.

The existing response schema, no-store policy, parameter binding, result ceiling,
query timeout, deterministic source/version ordering and state-bound pagination
remain. A publication change restarts pagination. Source catalog completeness
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
