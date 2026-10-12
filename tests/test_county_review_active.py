"""Executable v2 assessments, native-candidate guards and backward-compatible output."""

import hashlib
from dataclasses import replace
from datetime import date
from importlib.resources import files

import pytest
from pydantic import ValidationError
from test_county_review import county, evidence, snapshot

from lyme_gap_atlas_api.cdc_surveillance import IDENTITIES, PERIOD, RELEASE
from lyme_gap_atlas_api.county_review import ReviewConfig, load_config
from lyme_gap_atlas_api.county_review_active import (
    ActiveReviewConfig,
    EvidenceInputs,
    ReviewEvidenceLoader,
    evaluate_discordance,
    evaluate_human,
    evaluate_pairing,
    evaluate_state,
    evaluate_transition,
    load_active_config,
    published_vector,
)
from lyme_gap_atlas_api.models import SourceMetadata
from lyme_gap_atlas_api.public_contract import (
    CollectionEnvelope,
    CollectionLinks,
    CollectionMeta,
    GeographyIdentity,
    GeographyType,
    Observation,
    ObservationQuery,
    ValueState,
)
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError


def inputs(status: str = "Reported") -> EvidenceInputs:
    return EvidenceInputs(
        county().model_copy(
            update={
                "release_id": RELEASE,
                "scapularis_status": status,
                "pacificus_status": "No records",
            }
        )
    )


def published(item: EvidenceInputs) -> None:
    snap = snapshot(item.county)
    metadata = snap.metadata.model_copy(
        update={
            "release_id": RELEASE,
            "methodology_version": "semantic-1.0.0",
            "sources": [
                SourceMetadata(
                    key="tick",
                    label="CDC",
                    vintage="through 2025-12-31",
                    url="https://www.cdc.gov/ticks/",
                    note="Cumulative, not abundance.",
                )
            ],
        }
    )
    item.published = published_vector(item.county, replace(snap, metadata=metadata))


def observed(
    measure: str = "case_count_floor_2023",
    value: float | str | None = 5,
    state: ValueState = ValueState.OBSERVED,
) -> Observation:
    cumulative = measure != "case_count_floor_2023"
    return Observation.model_validate(
        {
            "observation_id": f"safe:{measure}",
            "measure_id": measure,
            "geography": {"geography_type": "county", "geography_id": "08001"},
            "period_start": PERIOD if cumulative else date(2023, 1, 1),
            "period_end": PERIOD if cumulative else date(2023, 12, 31),
            "temporal_grain": "CUMULATIVE" if cumulative else "YEAR",
            "value": value,
            "value_state": state,
            "unit": "count",
            "denominator": None,
            "source_id": "tick" if cumulative else "human",
            "dataset_id": "x5j9-wybp",
            "methodology_id": None,
            "methodology_version": "fixture",
            "semantic_version": "1.0.0",
            "release_id": RELEASE,
            "provenance_ref": f"safe:{measure}",
            "limitations": ["Floor."],
            "evidence": {
                "resource_type": "observation",
                "resource_id": f"safe:{measure}",
                "source_id": "human",
                "provenance_ref": f"safe:{measure}",
                "semantic_version": "1.0.0",
                "methodology_version": "fixture",
                "release_id": RELEASE,
            },
        }
    )


def test_immutable_v1_and_strict_v2_configuration() -> None:
    historical, digest = load_config()
    raw = (
        files("lyme_gap_atlas_api")
        .joinpath("county-review-methodology-v1.config.json")
        .read_bytes()
    )
    assert digest == hashlib.sha256(raw).hexdigest()
    assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == (
        "db6df73f7bce474000165fb2b0bad9767390c21aed160a1377be6a4893004d99"
    )
    active, active_digest = load_active_config()
    assert len(active_digest) == 64
    assert active.methodology_version == "2.0.0"
    assert all(r.enabled for r in active.rules.values())
    for change in ({"enabled": False}, {"lookback_periods": 3}, {"freshness_limit": 5}):
        payload = active.model_dump()
        payload["rules"]["human_emerging"].update(change)
        with pytest.raises(ValidationError):
            ActiveReviewConfig.model_validate(payload)
    payload = historical.model_dump()
    payload["rules"]["human_emerging"]["enabled"] = True
    with pytest.raises(ValidationError):
        ReviewConfig.model_validate(payload)


@pytest.mark.parametrize(
    "state", [ValueState.MISSING, ValueState.SUPPRESSED, ValueState.UNAVAILABLE]
)
def test_human_missing_is_not_zero(state: ValueState) -> None:
    item = inputs()
    item.observations = [observed(value=None, state=state)]
    result = evaluate_human(item)
    assert result.abstained and not result.evaluated and not result.candidate
    assert result.reason_codes == ["MISSING_HUMAN_SURVEILLANCE"]
    assert result.evidence[0].value_state == state


def test_human_positive_floor_assesses_history_without_inventing_trend() -> None:
    item = inputs()
    item.observations = [observed()]
    assert evaluate_human(item).reason_codes == ["NO_COMPARABLE_HISTORY"]
    prior = observed().model_copy(
        update={"period_start": date(2022, 1, 1), "period_end": date(2022, 12, 31)}
    )
    item.observations.append(prior)
    assert evaluate_human(item).reason_codes == ["FLOOR_CHANGE_NOT_IDENTIFIABLE"]


def test_vector_snapshots_need_pairwise_comparability() -> None:
    item = inputs()
    item.observations = [observed("scapularis_status", "Reported")]
    assert evaluate_transition(item).reason_codes == ["NO_COMPARABLE_NATIVE_SNAPSHOTS"]
    item.observations.append(
        item.observations[0].model_copy(
            update={
                "period_start": date(2024, 12, 31),
                "period_end": date(2024, 12, 31),
            }
        )
    )
    assert evaluate_transition(item).reason_codes == ["COMPARABILITY_UNKNOWN"]


def test_discordance_requires_both_positive_families_and_compatible_periods() -> None:
    item = inputs()
    item.observations = [observed()]
    assert evaluate_discordance(item).reason_codes == ["INSUFFICIENT_PAIRED_EVIDENCE"]
    item.observations.append(observed("scapularis_status", "Reported"))
    assert evaluate_discordance(item).reason_codes == ["INCOMPATIBLE_PERIODS"]
    item.observations[-1] = item.observations[-1].model_copy(
        update={
            "period_start": date(2023, 1, 1),
            "period_end": date(2023, 12, 31),
            "temporal_grain": "YEAR",
        }
    )
    assert evaluate_discordance(item).reason_codes == ["COMPARISON_CRITERION_UNAPPROVED"]


@pytest.mark.parametrize(
    "status,evaluated",
    [("Established", True), ("Reported", False), ("No records", False), ("Unknown", False)],
)
def test_d_published_species_fact_only_excludes(status: str, evaluated: bool) -> None:
    item = inputs(status)
    published(item)
    result = evaluate_pairing(item)
    assert result.evaluated == evaluated
    assert result.abstained != evaluated
    assert not result.candidate


def native_fixture(item: EvidenceInputs) -> None:
    native = evidence()
    native.county = item.county
    for ref in [native.pathogen, *native.vector.values()]:
        assert ref is not None
        ref.release_id = RELEASE
        ref.source_version = next(v[1] for v in IDENTITIES.values() if v[0] == ref.source_product)
    item.native = native


def test_d_qualifying_fixture_and_safe_candidate_aggregation() -> None:
    item = inputs()
    native_fixture(item)
    result = evaluate_pairing(item)
    assert result.candidate and result.evaluated and not result.abstained
    config, digest = load_active_config()
    snap = snapshot(item.county)
    snap = replace(snap, metadata=snap.metadata.model_copy(update={"release_id": RELEASE}))
    review = evaluate_state("CO", snap, [item], config, digest)
    assert review.result_state == "candidates_found"
    assert review.coverage.abstained_counties == review.coverage.evaluated_counties == 1
    assert len(review.review_candidates) == 1
    body = review.model_dump_json()
    assert "source_row_id" not in body and 'revision_id"' not in body
    for identity in IDENTITIES.values():
        assert identity[1] not in body


@pytest.mark.parametrize("failure", ["release", "period", "lineage", "source", "missing"])
def test_d_bad_native_inputs_abstain(failure: str) -> None:
    item = inputs()
    native_fixture(item)
    pathogen = item.native.pathogen
    assert pathogen is not None
    if failure == "release":
        pathogen.release_id = "other"
    elif failure == "period":
        pathogen.source_as_of = "2024-12-31"
    elif failure == "lineage":
        pathogen.revision_id = ""
    elif failure == "source":
        pathogen.source_version = "other"
    else:
        item.native.vector.clear()
    result = evaluate_pairing(item)
    assert result.abstained and not result.candidate and not result.evaluated


def test_d_complete_established_native_is_nonqualifying() -> None:
    item = inputs()
    native_fixture(item)
    item.native.vector["Ixodes scapularis"].status = "Established"
    result = evaluate_pairing(item)
    assert result.evaluated and not result.abstained and not result.candidate


def test_loader_atomic_identity_checks_and_no_membership_expansion() -> None:
    item = inputs()
    snap = snapshot(item.county)
    snap = replace(
        snap, metadata=snap.metadata.model_copy(update={"release_id": RELEASE, "sources": []})
    )

    class Observations:
        cdc_adapter = None

        def search(self, query: ObservationQuery) -> CollectionEnvelope[Observation]:
            assert query.geography_id == ["08001"]
            return CollectionEnvelope(
                data=[
                    observed(),
                    observed().model_copy(
                        update={
                            "geography": GeographyIdentity(
                                geography_type=GeographyType.county, geography_id="08003"
                            ),
                        }
                    ),
                ],
                meta=CollectionMeta(),
                links=CollectionLinks(),
            )

    from typing import cast

    from lyme_gap_atlas_api.public_observations import ObservationService

    loaded = ReviewEvidenceLoader(cast(ObservationService, Observations())).load("CO", snap)
    assert loaded[0].observations == []
    assert loaded[0].errors == ["case_count_floor_2023:EVIDENCE_DELIVERY_UNAVAILABLE"]


def test_county_counts_are_unions_and_rule_counts_are_separate() -> None:
    item = inputs("Established")
    published(item)
    config, digest = load_active_config()
    snap = snapshot(item.county)
    snap = replace(snap, metadata=snap.metadata.model_copy(update={"release_id": RELEASE}))
    result = evaluate_state("CO", snap, [item], config, digest)
    assert result.result_state == "insufficient_evidence"
    assert result.coverage.evaluated_counties == result.coverage.abstained_counties == 1
    assert result.rule_evaluation_counts["pathogen_present_vector_reported"].evaluated_counties == 1
    assert all(r.eligible_counties == 1 for r in result.rule_evaluation_counts.values())
    assert result.review_candidates == []
    item.county = item.county.model_copy(update={"release_id": "mismatch"})
    with pytest.raises(AtlasDataUnavailableError):
        evaluate_state("CO", snap, [item], config, digest)


def test_transport_failure_does_not_become_missing_human_evidence() -> None:
    item = inputs()
    item.errors = ["case_count_floor_2023:EVIDENCE_DELIVERY_UNAVAILABLE"]
    result = evaluate_human(item)
    assert result.reason_codes == ["EVIDENCE_DELIVERY_UNAVAILABLE"]
    assert result.abstained and not result.evaluated


def test_per_rule_cdc_delivery_failure_is_explicit() -> None:
    item = inputs()
    item.observations = [observed()]
    item.errors = ["scapularis_status:EVIDENCE_DELIVERY_UNAVAILABLE"]
    for evaluator in (evaluate_transition, evaluate_discordance, evaluate_pairing):
        result = evaluator(item)
        assert result.reason_codes == ["EVIDENCE_DELIVERY_UNAVAILABLE"]
        assert result.abstained and not result.evaluated
    item.errors = ["case_count_floor_2023:EVIDENCE_DELIVERY_UNAVAILABLE"]
    assert evaluate_discordance(item).reason_codes == ["EVIDENCE_DELIVERY_UNAVAILABLE"]
    item = inputs("Established")
    published(item)
    item.errors = ["scapularis_status:EVIDENCE_DELIVERY_UNAVAILABLE"]
    assert evaluate_pairing(item).evaluated


def test_established_requires_published_source_vintage_and_release() -> None:
    item = inputs("Established")
    snap = snapshot(item.county)
    snap = replace(
        snap,
        metadata=snap.metadata.model_copy(
            update={
                "release_id": RELEASE,
                "sources": [],
            }
        ),
    )
    assert published_vector(item.county, snap) == []
    assert not evaluate_pairing(item).evaluated
    published(item)
    item.county = item.county.model_copy(update={"in_contiguous_tick_scope": False})
    result = evaluate_pairing(item)
    assert result.reason_codes == ["OUT_OF_SCOPE"]
    assert not result.eligible and not result.evaluated and not result.abstained


@pytest.mark.parametrize("mutant", ["period", "release", "duplicate", "pagination"])
def test_loader_rejects_incompatible_or_incomplete_batches(mutant: str) -> None:
    from typing import cast

    from lyme_gap_atlas_api.public_observations import ObservationService

    item = inputs()
    snap = snapshot(item.county)
    snap = replace(
        snap,
        metadata=snap.metadata.model_copy(
            update={
                "release_id": RELEASE,
                "sources": [],
            }
        ),
    )

    class Observations:
        cdc_adapter = None

        def search(self, query: ObservationQuery) -> CollectionEnvelope[Observation]:
            row = observed()
            if mutant == "period":
                row = row.model_copy(update={"period_end": date(2024, 12, 31)})
            if mutant == "release":
                row = row.model_copy(update={"release_id": "other"})
            return CollectionEnvelope(
                data=[row, row] if mutant == "duplicate" else [row],
                meta=CollectionMeta(next_page_token="more" if mutant == "pagination" else None),
                links=CollectionLinks(),
            )

    loaded = ReviewEvidenceLoader(cast(ObservationService, Observations())).load("CO", snap)
    assert loaded[0].observations == []
    assert loaded[0].errors
