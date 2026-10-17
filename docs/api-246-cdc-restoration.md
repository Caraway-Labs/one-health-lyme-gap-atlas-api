# API #246: exact current-release CDC binding

This change prepares serving for `governed-2026-10-15-january-climate-prod`,
bundle `53e50aa37c701635e96c48cd4931c4c4a9e7e695c146263fb9bb335e541ae74d`.
Data contract review: [Data PR #697](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-data/pull/697).
The dependency is an immutable reviewed commit, never a moving branch. Its
admission deployment and real steward registration must complete before API
deployment. Final merge must verify that exact dependency commit is in Data main.

The adapter passes the exact release to the Data producer and requires the
reviewed revision-3 metadata identities. Data retains source/version, physical
record, semantic revision and genuine historical-receipt authorization. Prior
October 6/metadata-2 acceptance documents remain historical evidence; this
binding does not fall back to that release or weaken membership checks.

The endpoint, operation IDs, schemas, pagination and source-backed labels remain
unchanged. Both measures remain cumulative through 2025-12-31, semantic meaning
1.0.0. `No records` remains NO_RECORDS / unknown, never negative. Unsupported
annual filters remain structured 400; unavailable authority remains sanitized
503. API #237 gateway transformation is separate and unchanged.

## Required release acceptance

Follow Data's `docs/operations/api246-cdc-restoration.md`: exact reviewed Data
image, protected migration/admission, actual production consumer readback, then
human-authorized API merge/deployment. API merge triggers existing deployment.
Green fixtures and packaging do not prove production restoration. For API #246,
the owner's final direction requires available independent source review followed
by explicit human owner review. The unavailable `.agent` coordinator was not
invoked and is not recorded as completed. This task-specific direction does not
alter GitHub protections or establish a general review-policy exception. Required
checks, resolved conversations and explicit human merge remain required.

Use at most 30 anonymous serial requests, at least 0.3 seconds after completion;
no automatic retries, stop escalation on repeated 5xx or any rate limit. Begin
with one county per measure, then retained examples tick 13081, pathogen 19135
and NO_RECORDS 12047. Verify HTTP200 JSON, target release, cumulative dates,
publisher value, normalized evidence, source attribution and limitations. Test
2/5/10 county batches only after healthy singletons, pagination page_size=1,
invalid annual filters400, and representative human/climate/five context reads.
Record status, content type, timing, request ID and gateway headers. No national
coverage or 50-county performance claim follows from these bounded checks.

Do not close API #246 until real production CDC responses, compatibility,
preventive Data promotion gate and required approvals are evidenced on the issue.
