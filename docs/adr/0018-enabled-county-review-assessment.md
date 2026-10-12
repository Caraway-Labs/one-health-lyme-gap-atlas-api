# 0018: Enabled County Review assessment

Status: Proposed for owner review with API #229 implementation
Date: 2026-10-11
Decision owner: Atlas product/scientific owner

## Context

The owner directed implementation of four enabled rule paths with truthful
abstention when inputs are unavailable. Historical `atlas-county-review@1.0.0`
rejects enablement and passes summary wrappers without observation evidence.
The current governed release has 2023 human floors and cumulative CDC statuses;
the admitted CDC adapter supports three measure/county memberships. No approved
human-change threshold, vector transition policy or discordance criterion was
supplied by this activation direction.

## Decision

Introduce `atlas-county-review@2.0.0` with all four assessment paths enabled.
Keep the v1 config and engine unchanged. A major method version records the
changed eligibility/evaluation reporting and bounded nonqualification admission;
the HTTP contract change is additive, retaining the `StateReview` schema name,
existing required fields, result-state vocabulary and operation ID.

Reuse the release snapshot, bounded ObservationService and existing CDC adapter.
Do not expand CDC memberships, issue grants, copy a Data classifier or expose
private proof. Admit each observation batch atomically against the snapshot's
release, county, measure and period; delivery errors have a separate explicit
reason and never become scientific missing values.

For D, one published, release-bound species-specific `Established` fact logically
disproves the existing **neither species Established** predicate. Such a result
is an evaluated nonqualification, with a published county reference and explicit
summary limitation. It cannot qualify a candidate or a historical comparison.
Candidate qualification still uses the existing D evaluator and requires complete
same-county native references for pathogen and both taxa, exact pinned sources,
release, revision, safe provenance and known acquisition timestamps. Existing
consumer admission does not supply this full pairing. Public candidate source
version strings in v2 identify the known cumulative vintage, not private ingestion
version UUIDs; nullable freshness is exposed in the new assessment evidence, and
no missing timestamp is fabricated to satisfy the older candidate shape.

A evaluates current human value/missingness and baseline identifiability. B checks
same-target native snapshots and their source/method comparability. C checks both
observed families and annual/cumulative period compatibility. Missing prerequisites
produce rule-specific abstentions. More unbounded floors do not prove a trend;
even matching clocks alone do not approve a discordance criterion. v2 accepts no
unreviewed tuning parameters. Enabling assessment is not scientific approval of
a candidate comparator. Future scientific comparator/allowlist decisions require
a new reviewed method artifact, rather than changing this immutable version.

## Consequences and acceptance

`county_evaluations` and `rule_evaluation_counts` are additive optional response
fields. Existing consumers can continue reading the original counts and reasons.
Eligible means within the assessment universe, not adequate scientific evidence.
Evaluated means a qualifying/nonqualifying predicate decision was supported;
abstained means a decision lacked required evidence or criteria. County-level
counts are unions across rules, so a county can be evaluated for D and abstained
for A/B/C. Per-rule counts remove that ambiguity. Zero candidates does not mean
zero observed surveillance evidence or zero disease activity.

Automatic review-stage assignment remains false. Tier1 admission/release guards
are not changed. No browser logic, Snowflake writes, source-use expansion,
authentication change or deployment topology change is introduced.

## Rollout and rollback

Owner review, independent final-SHA review, required quality and explicit human
merge are required. No deployment is performed by this task. The existing
quality-gated main workflow will activate v2 on a later authorized deployment;
verify Alabama and Georgia using the deployed Review routes and actual service
identity then. Rollback through the prior reviewed API commit restores v1.0.0.

## References

- [API #229](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-api/issues/229)
- [Implementation and evidence](../api-229-county-review-v2.md)
- [CDC admission ADR 0017](0017-cdc-consumer-observations.md)
- [Canonical original method](https://github.com/Caraway-Labs/one-health-lyme-gap-atlas-web/blob/482d09d4be3ff6a09c5b2cd142f023dd66fe83d2/docs/product/county-review-methodology-v1.md)
