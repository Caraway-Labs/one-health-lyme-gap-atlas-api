"""Offline service/HTTP integration; injected authority is never PROD acceptance."""

import copy
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_public_observations import FixtureRepository, params, row
from test_surveillance_delivery import (
    BINDING,
    CONTEXT_OBSERVATION,
    CONTEXT_SLICE,
    HUMAN,
    HUMAN_SLICE,
    VECTOR,
    VECTOR_SLICE,
    admitted_evidence,
    alignment_delivery,
    comparison_delivery,
    evidence_delivery,
)

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_contract import SurveillanceEvidenceContext
from lyme_gap_atlas_api.surveillance_delivery import _evidence_validator, resolve_context


class Resolver:
    def __init__(self, delivery, selected=VECTOR_SLICE):
        self.delivery = delivery
        self.selected = selected

    def supported_slice(self, release_id):
        return self.selected

    def admitted_binding(self, release_id):
        return BINDING

    def resolve(self, observation):
        return self.delivery


def http_client(observation, resolver):
    class Repository(FixtureRepository):
        def measure_exists(self, measure_id, release):
            return measure_id == observation.measure_id

        def query(self, query, release, offset):
            result = list(row("08013", '"Reported"', "OBSERVED"))
            for index, value in {
                0: observation.observation_id,
                1: observation.measure_id,
                2: observation.geography.geography_id,
                4: observation.period_start,
                5: observation.period_end,
                6: observation.temporal_grain,
                7: json.dumps(observation.value),
                8: observation.value_state.value,
                25: observation.lineage_source_id,
            }.items():
                result[index] = value
            return [tuple(result)]

    return TestClient(
        create_app(
            settings=ApiSettings(),
            observation_repository=Repository(),
            surveillance_resolver=resolver,
        )
    )


@pytest.mark.parametrize("normalized", ["established", "detected", "unknown"])
def test_normalized_state_acceptance_and_publisher_value_preservation(normalized):
    envelope = admitted_evidence()
    envelope["surveillance_evidence"]["normalized_state"] = normalized
    original = copy.deepcopy(envelope)
    assert _evidence_validator.is_valid(envelope)
    api = http_client(VECTOR, Resolver(evidence_delivery(envelope)))
    response = api.get("/v1/observations", params=params(measure_id=VECTOR.measure_id, year=2025))
    assert response.status_code == 200
    item = response.json()["data"][0]
    assert item["value"] == "Reported"
    evidence = item["surveillance_context"]["evidence"]
    assert evidence["normalized_state"] == normalized
    assert evidence["state"] == "detected_below_establishment"
    assert envelope == original
    for private in ("authority_reference", "bundle_sha256", "source_version_id", "lineage_id"):
        assert private not in str(item["surveillance_context"])


def test_legacy_companion_does_not_derive_normalization_from_label_or_state():
    envelope = admitted_evidence()
    del envelope["surveillance_evidence"]["normalized_state"]
    assert _evidence_validator.is_valid(envelope)
    context = resolve_context(VECTOR, Resolver(evidence_delivery(envelope)))
    assert context.evidence.normalized_state is None
    legacy = context.evidence.model_dump(exclude_unset=True)
    assert "normalized_state" not in legacy
    assert (
        SurveillanceEvidenceContext.model_validate(legacy).state == "detected_below_establishment"
    )


@pytest.mark.parametrize("unsupported", ["absent", "negative", "sampled_not_detected", None, 0])
def test_strict_normalized_vocabulary_rejects_unsupported_values(unsupported):
    envelope = admitted_evidence()
    envelope["surveillance_evidence"]["normalized_state"] = unsupported
    assert not _evidence_validator.is_valid(envelope)
    api = http_client(VECTOR, Resolver(evidence_delivery(envelope)))
    assert (
        api.get(
            "/v1/observations", params=params(measure_id=VECTOR.measure_id, year=2025)
        ).status_code
        == 503
    )
    if unsupported is not None:
        with pytest.raises(ValidationError):
            SurveillanceEvidenceContext.model_validate(envelope["surveillance_evidence"])


@pytest.mark.parametrize("failure", ["release", "bundle", "authority", "internal", "missing"])
def test_http_admission_failures_never_publish_evidence(failure):
    envelope = admitted_evidence()
    envelope["surveillance_evidence"]["normalized_state"] = "detected"
    delivery = evidence_delivery(envelope)
    if failure == "internal":
        # Visibility is private producer authority, never an allowed consumer field.
        envelope["semantic"]["lineage"]["visibility"] = "INTERNAL"
    elif failure == "missing":
        delivery = None
    else:
        binding = {
            "release": replace(BINDING, release_id="stale"),
            "bundle": replace(BINDING, bundle_sha256="b" * 64),
            "authority": replace(BINDING, authority_reference=""),
        }[failure]
        delivery = replace(delivery, binding=binding)
    api = http_client(VECTOR, Resolver(delivery))
    response = api.get("/v1/observations", params=params(measure_id=VECTOR.measure_id, year=2025))
    assert response.status_code == 503
    assert "surveillance_context" not in response.text


@pytest.mark.parametrize("kind", ["methodology_unknown", "methodology_not_comparable", "alignment"])
def test_temporal_companions_reach_http_without_evidence_inference(kind):
    if kind == "alignment":
        observation, delivery, selected = CONTEXT_OBSERVATION, alignment_delivery(), CONTEXT_SLICE
    else:
        observation, delivery, selected = HUMAN, comparison_delivery(kind), HUMAN_SLICE
    api = http_client(observation, Resolver(delivery, selected))
    response = api.get("/v1/observations", params=params(measure_id=observation.measure_id))
    assert response.status_code == 200
    context = response.json()["data"][0]["surveillance_context"]
    assert context["evidence"] is None
    if kind == "alignment":
        assert context["temporal_alignment"]["lag"]["kind"] == "FIXED"
        assert context["methodology_comparison"] is None
    else:
        assert (
            context["methodology_comparison"]["comparison_state"]
            == {
                "methodology_unknown": "UNKNOWN",
                "methodology_not_comparable": "NOT_COMPARABLE",
            }[kind]
        )
        assert context["temporal_alignment"] is None


def test_default_app_has_no_resolver_and_old_observations_remain_valid():
    api = TestClient(create_app(settings=ApiSettings(), observation_repository=FixtureRepository()))
    assert api.app.state.observation_service.surveillance_resolver is None
    response = api.get("/v1/observations", params=params())
    assert response.status_code == 200
    assert all(item["surveillance_context"] is None for item in response.json()["data"])


def test_public_and_first_party_openapi_include_optional_strict_context():
    app = create_app(settings=ApiSettings(), observation_repository=FixtureRepository())
    for contract in (app.openapi(), app.first_party_openapi()):
        schemas = contract["components"]["schemas"]
        observation = schemas["Observation"]
        assert "surveillance_context" not in observation["required"]
        evidence = schemas["SurveillanceEvidenceContext"]
        assert evidence["additionalProperties"] is False
        assert "normalized_state" not in evidence["required"]
        assert evidence["properties"]["normalized_state"]["enum"] == [
            "established",
            "detected",
            "unknown",
            None,
        ]
