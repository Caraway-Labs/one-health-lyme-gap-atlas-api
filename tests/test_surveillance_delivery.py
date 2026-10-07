"""Offline API 88 boundary checks against captured Data projector fixtures."""

import copy
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from test_public_observations import row

from lyme_gap_atlas_api.public_contract import GeographyIdentity, GeographyType
from lyme_gap_atlas_api.public_observations import ObservationService
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError
from lyme_gap_atlas_api.surveillance_delivery import (
    AlignmentDelivery,
    BundleBinding,
    ComparisonDelivery,
    ComparisonProducerBinding,
    EvidenceDelivery,
    SourceIdentity,
    SupportedSlice,
    SurveillanceDelivery,
    _evidence_validator,
    admit_surveillance_context,
    resolve_context,
)

FIXTURES = Path(__file__).parent / "fixtures"
DATA429 = json.loads((FIXTURES / "api88_data429_projector.json").read_text())
COMPANIONS = json.loads((FIXTURES / "api88_data430_431_projectors.json").read_text())
BINDING = BundleBinding("release-1", "a" * 64, "existing-data-authority")
VECTOR_SOURCE = "cdc_arbonet_tick_module"
VECTOR = ObservationService._observation(row("08013", '"Reported"', "OBSERVED"))
VECTOR = VECTOR.model_copy(
    update={
        "observation_id": "fixture-vector-public-id",
        "measure_id": "scapularis_status",
        "geography": GeographyIdentity(
            geography_type=GeographyType.county, geography_id="08013"
        ),
        "period_start": date(2025, 12, 31),
        "period_end": date(2025, 12, 31),
        "temporal_grain": "CUMULATIVE",
        "lineage_source_id": VECTOR_SOURCE,
        "unit": "status",
    }
)
VECTOR_SLICE = SupportedSlice(
    "scapularis_status", VECTOR_SOURCE, "CUMULATIVE",
    date(2025, 12, 31), date(2025, 12, 31),
    frozenset({VECTOR.value_state}), "scapularis_status", "CUMULATIVE_THROUGH_DATE",
)


def admitted_evidence():
    """Synthetic Data output with an injected admitted binding; never live proof."""
    envelope = copy.deepcopy(DATA429["detected_below_establishment"])
    envelope["semantic"]["evidence_tier"] = "REVIEWED_CONTRACT"
    envelope["surveillance_evidence"]["evidence_tier"] = "GOVERNED_MAPPING"
    envelope["semantic"]["release"] = {
        "id": BINDING.release_id, "bundle_sha256": BINDING.bundle_sha256
    }
    envelope["semantic"]["lineage"]["release_id"] = BINDING.release_id
    return envelope


def evidence_delivery(envelope=None):
    envelope = admitted_evidence() if envelope is None else envelope
    source = envelope["semantic"]["provenance"]
    observation = envelope["semantic"]["observation"]
    identity = SourceIdentity(
        observation["id"], observation["revision_id"],
        source["source_id"]["value"], source["source_version_id"]["value"],
    )
    return SurveillanceDelivery(
        BINDING, VECTOR.observation_id, identity,
        evidence=EvidenceDelivery(identity, envelope),
    )


def admit_vector(envelope=None, *, delivery=None):
    return admit_surveillance_context(
        VECTOR, delivery or evidence_delivery(envelope), BINDING, VECTOR_SLICE
    )


def test_data429_projector_fixtures_are_schema_valid_but_not_admitted():
    assert set(DATA429) == {
        "established", "detected_below_establishment", "sampled_not_detected",
        "no_qualifying_record", "unknown",
    }
    for state, envelope in DATA429.items():
        assert _evidence_validator.is_valid(envelope)
        assert envelope["surveillance_evidence"]["state"] == state
        assert envelope["semantic"]["provenance"]["source_id"]["state"] == "KNOWN"
        assert envelope["surveillance_evidence"]["evidence_tier"] == "SYNTHETIC_FIXTURE"
    with pytest.raises(AtlasDataUnavailableError):
        admit_vector(delivery=evidence_delivery(DATA429["detected_below_establishment"]))


def test_producer_shaped_evidence_binding_and_privacy():
    context = admit_vector()
    assert context.evidence.state == "detected_below_establishment"
    public = context.model_dump(mode="json")
    for private in ("authority_reference", "bundle_sha256", "source_version_id", "lineage_id"):
        assert private not in str(public)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["semantic"]["provenance"].update(source_id={"state": "UNKNOWN", "value": None}),
        lambda p: p["semantic"].update(provenance=None),
        lambda p: p["semantic"].update(provenance=[]),
        lambda p: p["semantic"]["measure"].update(id="other"),
        lambda p: p["semantic"]["observation"]["geography"].update(county_fips="01001"),
        lambda p: p["semantic"]["observation"]["temporal"].update(date="2024-12-31"),
        lambda p: p["semantic"]["observation"].update(revision_id="stale"),
        lambda p: p["semantic"]["observation"].update(value_state="ZERO"),
        lambda p: p["semantic"]["release"].update(bundle_sha256="b" * 64),
        lambda p: p.update(contract_version="atlas-surveillance-evidence-consumer-v1"),
        lambda p: p["surveillance_evidence"].update(evidence_revision_id="bad"),
    ],
)
def test_evidence_shape_identity_and_release_mismatch_denies(mutate):
    envelope = admitted_evidence()
    mutate(envelope)
    with pytest.raises(AtlasDataUnavailableError):
        admit_vector(delivery=replace(evidence_delivery(), evidence=EvidenceDelivery(
            evidence_delivery().primary_identity, envelope
        )))


def test_unsupported_value_and_grain_denied_before_resolution():
    class Resolver:
        def supported_slice(self, release_id):
            return VECTOR_SLICE

        def admitted_binding(self, release_id):
            return BINDING

        def resolve(self, observation):
            return evidence_delivery()

    assert resolve_context(VECTOR, Resolver()).evidence is not None
    with pytest.raises(AtlasDataUnavailableError):
        resolve_context(VECTOR.model_copy(update={"temporal_grain": "YEAR"}), Resolver())
    with pytest.raises(AtlasDataUnavailableError):
        resolve_context(
            VECTOR.model_copy(update={"value_state": "NO_RECORDS"}), Resolver()
        )
    class Unresolved(Resolver):
        def resolve(self, observation):
            return None

    with pytest.raises(AtlasDataUnavailableError):
        resolve_context(VECTOR, Unresolved())


HUMAN = ObservationService._observation(row("42003", "12", "OBSERVED"))
HUMAN = HUMAN.model_copy(
    update={"period_start": date(2022, 1, 1), "period_end": date(2022, 12, 31)}
)
LEFT = SourceIdentity("left-key", "revision-1", "cdc_lyme", "approved-current")
RIGHT = SourceIdentity("right-key", "revision-1", "cdc_lyme", "approved-current")


def comparison_delivery(kind="methodology"):
    projection = copy.deepcopy(
        COMPANIONS[kind]["projection"] if kind == "methodology" else COMPANIONS[kind]
    )
    historical = kind != "methodology"
    left = replace(LEFT, source_version_id="approved-historical") if historical else LEFT
    left_resource = "cdc_lyme_qtbi_xd4i" if historical else "cdc_lyme_x5j9_wybp"
    right_historical = kind == "methodology_caution_version"
    right = (
        replace(RIGHT, source_version_id="approved-historical")
        if right_historical else RIGHT
    )
    right_resource = "cdc_lyme_qtbi_xd4i" if right_historical else "cdc_lyme_x5j9_wybp"
    left_class = "LOW" if kind == "methodology_caution_changed" else "HIGH"
    if kind == "methodology_unknown":
        left_class = "UNKNOWN"
    reference = (
        "https://example.org/fixture-reviewed-jurisdiction-evidence"
        if kind in {
            "methodology_caution_changed", "methodology_caution_version",
            "methodology_not_comparable",
        }
        else None
    )
    certified = ComparisonProducerBinding(
        left, right, left_resource, right_resource,
        "Probable", "Probable",
        "left-public-id", projection["jurisdiction_class"],
        left_class, "HIGH", reference, projection,
    )
    companion = ComparisonDelivery(
        left, right, certified.left_resource_key, certified.right_resource_key,
        reference, "left-public-id", projection, certified,
    )
    return SurveillanceDelivery(BINDING, HUMAN.observation_id, right, comparison=companion)


def test_data430_ordered_pair_and_certified_context():
    item = comparison_delivery()
    result = admit_surveillance_context(HUMAN, item, BINDING, VECTOR_SLICE)
    assert result.methodology_comparison.comparison_state == "COMPARABLE"
    assert result.methodology_comparison.left_observation_id == "left-public-id"
    original = item.comparison
    for changed in (
        replace(original, left=replace(LEFT, observation_key="other")),
        replace(original, left=replace(LEFT, revision_id="stale")),
        replace(original, left=replace(LEFT, source_version_id="other")),
        replace(original, left_public_observation_id="wrong"),
        replace(original, projection=[]),
        replace(original, producer_binding=replace(
            original.producer_binding, left_case_category=None
        )),
        replace(original, producer_binding=replace(
            original.producer_binding, jurisdiction_class="UNKNOWN"
        )),
    ):
        with pytest.raises(AtlasDataUnavailableError):
            admit_surveillance_context(
                HUMAN, replace(item, comparison=changed), BINDING, VECTOR_SLICE
            )


@pytest.mark.parametrize(
    ("kind", "state", "jurisdiction"),
    [
        ("methodology", "COMPARABLE", "HIGH"),
        ("methodology_caution_changed", "CAUTION_REQUIRED", "UNKNOWN"),
        ("methodology_caution_version", "CAUTION_REQUIRED", "HIGH"),
        ("methodology_not_comparable", "NOT_COMPARABLE", "HIGH"),
        ("methodology_unknown", "UNKNOWN", "UNKNOWN"),
    ],
)
def test_data430_producer_comparison_state_matrix(kind, state, jurisdiction):
    item = comparison_delivery(kind)
    result = admit_surveillance_context(HUMAN, item, BINDING, VECTOR_SLICE)
    assert result.methodology_comparison.comparison_state == state
    assert result.methodology_comparison.jurisdiction_class == jurisdiction
    if kind == "methodology_caution_changed":
        assert result.methodology_comparison.reason_codes == ["JURISDICTION_CLASS_CHANGED"]
        with pytest.raises(AtlasDataUnavailableError):
            admit_surveillance_context(
                HUMAN,
                replace(item, comparison=replace(
                    item.comparison,
                    producer_binding=replace(
                        item.comparison.producer_binding,
                        jurisdiction_evidence_reference=None,
                    ),
                )),
                BINDING, VECTOR_SLICE,
            )


ALIGNMENT = COMPANIONS["alignment_discrete"]
ALIGNMENT_INPUT = SourceIdentity(
    "alignment-key", "synthetic-revision", "synthetic-source", "synthetic-version"
)
CONTEXT_OBSERVATION = HUMAN.model_copy(
    update={"measure_id": "county-context", "lineage_source_id": "synthetic-source"}
)


def alignment_delivery(projection=None):
    projection = copy.deepcopy(ALIGNMENT if projection is None else projection)
    companion = AlignmentDelivery(
        ALIGNMENT_INPUT, projection["method_id"], projection["method_version"],
        projection["observation_window"], projection["target_window"],
        projection["decision_cutoff"], projection.get("reference_period"), projection,
    )
    return SurveillanceDelivery(
        BINDING, CONTEXT_OBSERVATION.observation_id, ALIGNMENT_INPUT,
        alignment=companion,
    )


def test_data431_discrete_and_fractional_area_round_trip():
    for name, expected in (("alignment_discrete", 31), ("alignment_fractional_area", 100.5)):
        context = admit_surveillance_context(
            CONTEXT_OBSERVATION,
            alignment_delivery(COMPANIONS[name]), BINDING, VECTOR_SLICE,
        )
        assert context.temporal_alignment.disposition == "ELIGIBLE"
        assert context.temporal_alignment.coverage.expected == expected
        assert (
            context.model_dump(mode="json")["temporal_alignment"]["coverage"]["expected"]
            == expected
        )


@pytest.mark.parametrize(
    "nested", ["observation_window", "target_window", "lag", "coverage", "reference_period"]
)
def test_data431_nested_private_fields_denied(nested):
    projection = copy.deepcopy(ALIGNMENT)
    if nested == "reference_period":
        projection[nested] = {
            "id": "fixture-reference", "start": "2024-01-01",
            "end_exclusive": "2025-01-01", "source_vintage": "fixture-vintage",
        }
    projection[nested]["physical_artifact_id"] = "private-lineage-proof"
    with pytest.raises(AtlasDataUnavailableError):
        admit_surveillance_context(
            CONTEXT_OBSERVATION,
            alignment_delivery(projection), BINDING, VECTOR_SLICE,
        )


def test_data431_declaration_mismatch_denied():
    item = alignment_delivery()
    companion = item.alignment
    for changed in (
        replace(companion, input=replace(ALIGNMENT_INPUT, revision_id="stale")),
        replace(companion, decision_cutoff="2026-01-01T00:00:00Z"),
        replace(companion, reference_period={"id": "other"}),
    ):
        with pytest.raises(AtlasDataUnavailableError):
            admit_surveillance_context(
                CONTEXT_OBSERVATION, replace(item, alignment=changed),
                BINDING, VECTOR_SLICE,
            )
