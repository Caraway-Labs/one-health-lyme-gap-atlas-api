# 0016: Authenticated data-rights execution

**Status:** Accepted  
**Date:** 2026-09-11  
**Decision owner:** One Health Lyme Gap Atlas product and engineering leads

## Context

Authenticated **Export my data** and **Remove all data** must cover live
optional-account records without adding a DigitalOcean worker, object store, or
Amplitude account identity. The product contract lives in the web repository
`docs/data-rights-workflow.md`.

## Decision

Add private `/v1/me/privacy-requests*` routes to this API. Persist a service-role
ledger in `atlas_accounts.privacy_requests`. Execute connected processors
(profile read, Auth-user export metadata, Auth-user delete) synchronously on
confirm. Unconnected processors are recorded as `omissions[]`. Require a token
`iat` no older than 15 minutes. Do not cascade-delete the ledger when the Auth
user is removed.

## Consequences

The OpenAPI contract gains additive private operations. Public Atlas routes,
Snowflake access, and CORS remain unchanged except as needed for these POST/GET
paths. Amplitude account linkage remains forbidden.

## Alternatives considered

A new worker, BackgroundTasks-only execution, and Amplitude identity stitching
were rejected; see the workspace copy of this ADR.

## Acceptance criteria

Contract tests reject unauthenticated, cross-user, expired, replayed, and stale
session attempts. Export JSON uses `atlas-user-data-export/v1`. Deletion does
not mutate public Atlas datasets.

## Rollout, observability, and rollback

Migrate Development, then Production. Deploy API before web UI. Logs may include
request id, action, processor, and redacted error class only.

## Links to affected contracts and tests

- `supabase/migrations/20260911120000_create_atlas_privacy_requests.sql`
- `tests/test_privacy_requests.py`
