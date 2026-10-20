# 0020: Explicit scoring v1.1 compatibility boundary

Status: Accepted
Date: 2026-10-19
Decision owner: Product owner, Shared Python #32

## Context
Old score settings use an information-destroying hard incidence cutoff. New
settings must not reinterpret breakpoint=10 as smooth k=10.

## Decision
Apply the approved Smooth k50 methodology. Add separate scoring version metadata
and authorized original scenario inputs to the existing first-party score DTO.
Preserve data-release identifiers. Retain the legacy query parameter as deprecated
with explicit 422 guidance; use smooth_k for v1.1. Updated Web rejects mismatched
score versions. Preserve all existing ML endpoints and predictions.

## Consequences
Old score clients require refresh/upgrade. No data or deployment topology change.
Parameter deprecation starts October 19, 2026; it remains documented through at
least April 19, 2027. Removal requires another reviewed change. Do not silently
serve a different formula to a client requesting the legacy breakpoint.

## Alternatives considered
Reusing the old parameter or inferring incidence would violate #32.

## Acceptance criteria
Numerical research parity, generated schemas, typed original scenario inputs,
contract/cache/export tests, independent review and coordinated live acceptance.

## Rollout, observability, and rollback
See [the coordinated release runbook](../gap-score-v11-release.md).
