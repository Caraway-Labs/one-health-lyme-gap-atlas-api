"""Deterministic Review rule and state precedence fixtures."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lyme_gap_atlas_api.county_review import (
    CountyEvidence,
    ReviewConfig,
    ReviewEvidenceReference,
    RuleConfig,
    aggregate_state,
    evaluate_d,
    load_config,
)
from lyme_gap_atlas_api.models import AtlasMetadata, CountyRecord
from lyme_gap_atlas_api.repository import Snapshot


def county(fips: str = "08001", scope: bool = True) -> CountyRecord:
    return CountyRecord.model_construct(
        release_id="release-1",
        fips=fips,
        county="Fixture County",
        state="CO",
        in_contiguous_tick_scope=scope,
    )


def ref(family: str, target: str, status: str, fips: str = "08001") -> ReviewEvidenceReference:
    return ReviewEvidenceReference(
        family=family,
        source_product=(
            "cdc-ixodes-pathogen-status-2025"
            if family == "pathogen"
            else "cdc-ixodes-county-status-2025"
        ),
        source_version="2025",
        source_row_id=f"row-{target}",
        revision_id="rev-1",
        county_fips=fips,
        release_id="release-1",
        source_as_of="2025-12-31",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=target,
        status=status,
    )


def evidence(
    fips: str = "08001",
    pathogen: str = "Present",
    scapularis: str = "Reported",
    pacificus: str = "No records",
) -> CountyEvidence:
    return CountyEvidence(
        county=county(fips),
        pathogen=ref("pathogen", "Borrelia burgdorferi sensu stricto", pathogen, fips),
        vector={
            "Ixodes scapularis": ref("vector", "Ixodes scapularis", scapularis, fips),
            "Ixodes pacificus": ref("vector", "Ixodes pacificus", pacificus, fips),
        },
    )


def enabled_d() -> RuleConfig:
    config, _ = load_config()
    return config.rules["pathogen_present_vector_reported"].model_copy(
        update={
            "enabled": True,
            "eligible_source_versions": {
                "cdc-ixodes-county-status-2025": "2025",
                "cdc-ixodes-pathogen-status-2025": "2025",
            },
            "source_use_approval_reference": "fixture-only",
        }
    )


def snapshot(*counties: CountyRecord) -> Snapshot:
    return Snapshot(
        metadata=AtlasMetadata.model_construct(release_id="release-1"), counties=list(counties)
    )


def test_initial_config_is_disabled_and_hashed() -> None:
    config, digest = load_config()
    assert len(digest) == 64
    assert not any(rule.enabled for rule in config.rules.values())
    result = aggregate_state("CO", snapshot(county()), [evidence()], config, digest)
    assert result.result_state == "unsupported"
    assert result.review_candidates == []
    assert result.data_gaps[0].code == "SOURCE_NATIVE_LINEAGE_UNAVAILABLE"


def test_config_rejects_unknown_rules_and_unapproved_activation() -> None:
    config, _ = load_config()
    payload = config.model_dump()
    payload["rules"]["arbitrary_expression"] = {"enabled": False}
    with pytest.raises(ValidationError):
        ReviewConfig.model_validate(payload)
    payload = config.model_dump()
    payload["rules"]["pathogen_present_vector_reported"]["enabled"] = True
    with pytest.raises(ValidationError):
        ReviewConfig.model_validate(payload)


@pytest.mark.parametrize(
    "pathogen,scapularis,pacificus,expected",
    [
        ("Present", "Reported", "No records", "candidate"),
        ("Present", "Established", "Reported", "negative"),
        ("Unknown", "Reported", "No records", "abstain"),
        ("No records", "Reported", "No records", "abstain"),
        ("Present", "No records", "No records", "abstain"),
    ],
)
def test_d_native_statuses(pathogen: str, scapularis: str, pacificus: str, expected: str) -> None:
    result = evaluate_d(
        evidence(pathogen=pathogen, scapularis=scapularis, pacificus=pacificus), enabled_d()
    )
    assert (
        "candidate" if result.candidate else "abstain" if result.abstained else "negative"
    ) == expected


def test_d_fails_closed_on_provenance_and_scope() -> None:
    missing_approval = enabled_d().model_copy(update={"source_use_approval_reference": None})
    assert evaluate_d(evidence(), missing_approval).gaps[0].code == "RULE_CONFIGURATION_UNAPPROVED"
    item = evidence()
    assert item.pathogen is not None
    item.pathogen.source_product = "unapproved"
    assert evaluate_d(item, enabled_d()).gaps[0].code == "SOURCE_PROVENANCE_MISMATCH"
    item = evidence()
    assert item.pathogen is not None
    item.pathogen.source_version = "other"
    assert evaluate_d(item, enabled_d()).gaps[0].code == "SOURCE_PROVENANCE_MISMATCH"
    item = evidence()
    item.county = county(scope=False)
    assert evaluate_d(item, enabled_d()).gaps[0].code == "OUT_OF_SCOPE"
    item = evidence()
    item.diagnostics = ["REVISION_CONFLICT"]
    assert evaluate_d(item, enabled_d()).gaps[0].code == "REVISION_CONFLICT"


def test_state_candidate_does_not_hide_abstention_or_gap() -> None:
    config, digest = load_config()
    config = config.model_copy(
        update={"rules": {**config.rules, "pathogen_present_vector_reported": enabled_d()}}
    )
    items = [evidence("08001"), evidence("08003"), evidence("08005")]
    items[1].pathogen = None
    items[2].vector = {}
    result = aggregate_state(
        "CO", snapshot(*(item.county for item in items)), items, config, digest
    )
    assert result.result_state == "candidates_found"
    assert len(result.review_candidates) == 1
    assert result.coverage.abstained_counties == 2
    assert len(result.data_gaps) == 2


def test_none_stand_out_requires_complete_enabled_evaluation() -> None:
    config, digest = load_config()
    config = config.model_copy(
        update={"rules": {**config.rules, "pathogen_present_vector_reported": enabled_d()}}
    )
    negative = evidence(scapularis="Established")
    result = aggregate_state("CO", snapshot(negative.county), [negative], config, digest)
    assert result.result_state == "none_stand_out"
    missing = evidence()
    missing.pathogen = None
    result = aggregate_state("CO", snapshot(missing.county), [missing], config, digest)
    assert result.result_state == "insufficient_evidence"
