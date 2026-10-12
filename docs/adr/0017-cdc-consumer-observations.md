# 0017: Approved CDC consumer observations

Status: Accepted (owner authorization in API #88 execution, 2026-10-11)
Date: 2026-10-11
Decision owner: Atlas owner

## Context

DATA #429 is complete. Its accepted consumer returns canonical `NO_RECORDS`
with the literal `No records`; public V1 previously could not represent that
state. The presentation observations view has no CDC rows. The reviewed
consumer-safe metadata revision also had no observation field.

## Decision

Add `NO_RECORDS` to the existing ValueState enum and optional
`Observation.metadata_revision_id`. Preserve all existing states and fields.
The owner explicitly approved these additive contract changes during execution.
No new endpoint, classification, authentication model or deployment topology.

Package Data at exact reviewed commit
`8ce5d47d518161986d20954f3ddceb8f66504c92`. Invoke its existing
`cdc_consumer_runtime._project` and `_resources`, the exact producer used by
`read_cdc_consumer`, using existing service key-pair settings. The convenience
reader fixes its connection timeout at 30 seconds and has no timeout argument.
The API therefore supplies its existing connection and query callback to that
same producer, preserving both API's configured statement limit and Data's
30-second cap, disabling secondary roles and leaving all admission in Data.
The exact package pin bounds the private-function coupling; no projector code
or private authority SQL is duplicated in the API.
Keep the API's existing shared-Python commit via uv's dependency override:
the connection interface is unchanged and binding/package tests validate it.
Data's full package brings its existing geospatial dependencies; no producer
code or authority resources are copied into the API.

Reuse the optional SurveillanceResolver and release-bound admission checks.
Use the existing observations endpoint and ObservationService. The CDC adapter
only accepts the three retained measure/county memberships and cumulative date;
other measures retain the existing repository path.

## Consequences and acceptance

Clients with exhaustive enum switches must regenerate and handle NO_RECORDS.
The literal publisher value remains available; normalized unknown never means
absent. Metadata revision is public reviewed identity; source versions, bundle
proof, authority and raw records remain private.

Focused tests cover accepted cases, rejected source/release/metadata identities,
private proof, credentials, unavailable producer, pagination and compatibility.
Production acceptance still requires actual service-key reads and HTTP probes.

## Rollout and rollback

`CDC_SURVEILLANCE_ENABLED` defaults false. Human merge follows independent
peer review and current quality. After protected deployment, confirm runtime
identity and consumer access, then set the existing runtime flag true and verify
the three genuine HTTP requests. Disable the flag to roll back activation.
Missing permissions follow Data's existing reviewed role-consolidation and
protected security provisioning procedure; this change issues no grants.

## References

- [Verified Data handoff](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/88#issuecomment-6114068780)
- [Public contract](../public-api-v1-contract.md)
- [Release and Web handoff](../api-88-production-cdc.md)
- `tests/test_cdc_surveillance.py`

## API #228 implementation correction (2026-10-11)

The owner explicitly requested removal of the three-example API restriction
in [API #228](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/228).
This supersedes only the API county-list restriction above: the same pinned
producer must establish exact published observation/record/semantic-revision
membership for every requested county. The two supported measures, cumulative
date, release/bundle, metadata identities, authentication and public schema remain
unchanged. No scientific or publication authority is broadened by this correction.
Additional coverage depends on retained Data-owned record admission; a published
source or complete lineage is insufficient. See the #228 acceptance record.
