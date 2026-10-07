"""Disabled-by-default API 88 seam for already admitted Data projections.

The resolver is a trusted repository boundary, not a scientific or approval engine.
No production resolver is configured until Data publishes its delivery binding.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from typing import Any, Protocol, TypeVar, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError
from referencing import Registry, Resource

from .public_contract import (
    MethodologyComparisonContext,
    Observation,
    SurveillanceContext,
    SurveillanceEvidenceContext,
    TemporalAlignmentContext,
    ValueState,
)
from .repository import AtlasDataUnavailableError


@dataclass(frozen=True)
class SourceIdentity:
    observation_key: str
    revision_id: str
    source_id: str
    source_version_id: str


@dataclass(frozen=True)
class BundleBinding:
    release_id: str
    bundle_sha256: str
    authority_reference: str  # Existing Data authority handle; never serialized.


@dataclass(frozen=True)
class SupportedSlice:
    """Exact reviewed repository slice; no annual/site/time coercion."""

    measure_id: str
    source_id: str
    temporal_grain: str
    period_start: date
    period_end: date
    value_states: frozenset[ValueState]
    semantic_measure_id: str
    semantic_temporal_semantics: str


@dataclass(frozen=True)
class EvidenceDelivery:
    identity: SourceIdentity
    envelope: Mapping[str, Any]


@dataclass(frozen=True)
class ComparisonDelivery:
    left: SourceIdentity
    right: SourceIdentity
    left_resource_key: str
    right_resource_key: str
    jurisdiction_evidence_reference: str | None
    left_public_observation_id: str
    projection: Mapping[str, Any]
    producer_binding: "ComparisonProducerBinding"


@dataclass(frozen=True)
class ComparisonProducerBinding:
    """Existing Data projector input/context proof carried by the trusted resolver."""

    left: SourceIdentity
    right: SourceIdentity
    left_resource_key: str
    right_resource_key: str
    left_case_category: str | None
    right_case_category: str | None
    left_public_observation_id: str
    jurisdiction_class: str
    jurisdiction_evidence_reference: str | None
    projection: Mapping[str, Any]


@dataclass(frozen=True)
class AlignmentDelivery:
    input: SourceIdentity
    method_id: str
    method_version: str
    observation_window: Mapping[str, Any]
    target_window: Mapping[str, Any]
    decision_cutoff: str
    reference_period: Mapping[str, Any] | None
    projection: Mapping[str, Any]


@dataclass(frozen=True)
class SurveillanceDelivery:
    binding: BundleBinding
    public_observation_id: str
    primary_identity: SourceIdentity
    evidence: EvidenceDelivery | None = None
    comparison: ComparisonDelivery | None = None
    alignment: AlignmentDelivery | None = None


class SurveillanceResolver(Protocol):
    """Data-backed resolver must return only existing projector-admitted bundles."""

    def admitted_binding(self, release_id: str) -> BundleBinding: ...

    def supported_slice(self, release_id: str) -> SupportedSlice: ...

    def resolve(self, observation: Observation) -> SurveillanceDelivery: ...


_contracts = files("lyme_gap_atlas_api")
_semantic_schema = json.loads(
    _contracts.joinpath("atlas-semantic-consumer-v1.schema.json").read_text(encoding="utf-8")
)
_evidence_schema = json.loads(
    _contracts.joinpath("surveillance-evidence-consumer-v2.schema.json").read_text(
        encoding="utf-8"
    )
)
_evidence_validator = Draft202012Validator(
    _evidence_schema,
    registry=Registry().with_resource(
        _semantic_schema["$id"], Resource.from_contents(_semantic_schema)
    ),
    format_checker=Draft202012Validator.FORMAT_CHECKER,
)


def resolve_context(
    observation: Observation, resolver: SurveillanceResolver
) -> SurveillanceContext:
    """Inactive adapter entrypoint for a future reviewed observation projection."""
    try:
        selected = resolver.supported_slice(observation.release_id)
        _require(
            selected is not None
            and observation.geography.geography_type.value == "county"
            and observation.measure_id == selected.measure_id
            and observation.lineage_source_id == selected.source_id
            and observation.temporal_grain == selected.temporal_grain
            and observation.period_start == selected.period_start
            and observation.period_end == selected.period_end
            and observation.value_state in selected.value_states
        )
        admitted = resolver.admitted_binding(observation.release_id)
        delivery = resolver.resolve(observation)
        if admitted is None or delivery is None:
            raise AtlasDataUnavailableError("Governed surveillance context is unavailable")
        return admit_surveillance_context(observation, delivery, admitted, selected)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise AtlasDataUnavailableError("Governed surveillance context is unavailable") from exc


def _require(condition: bool) -> None:
    if not condition:
        raise AtlasDataUnavailableError("Governed surveillance context is unavailable")


def _object(value: Any) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping))
    return cast(Mapping[str, Any], value)


def _known(value: Any) -> str:
    wrapper = _object(value)
    _require(set(wrapper) == {"state", "value"})
    _require(wrapper.get("state") == "KNOWN")
    result = wrapper.get("value")
    _require(isinstance(result, str) and bool(result))
    return cast(str, result)


ModelT = TypeVar("ModelT", bound=BaseModel)


def _safe_model(model: type[ModelT], value: Mapping[str, Any]) -> ModelT:
    try:
        return model.model_validate(value)
    except (ValidationError, ValueError, TypeError) as exc:
        raise AtlasDataUnavailableError("Governed surveillance context is unavailable") from exc


def admit_surveillance_context(
    observation: Observation,
    delivery: SurveillanceDelivery,
    admitted: BundleBinding,
    selected: SupportedSlice,
) -> SurveillanceContext:
    """Pair Data's safe projections with the exact admitted release and row."""
    try:
        return _admit_surveillance_context(observation, delivery, admitted, selected)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise AtlasDataUnavailableError("Governed surveillance context is unavailable") from exc


def _admit_surveillance_context(
    observation: Observation,
    delivery: SurveillanceDelivery,
    admitted: BundleBinding,
    selected: SupportedSlice,
) -> SurveillanceContext:
    binding = delivery.binding
    _require(
        bool(binding.release_id and binding.bundle_sha256 and binding.authority_reference)
        and binding == admitted
        and binding.release_id == observation.release_id
        and delivery.public_observation_id == observation.observation_id
        and delivery.primary_identity.source_id == observation.lineage_source_id
        and bool(
            delivery.primary_identity.observation_key and delivery.primary_identity.revision_id
        )
        and any((delivery.evidence, delivery.comparison, delivery.alignment))
    )
    evidence = None
    if delivery.evidence is not None:
        envelope = _object(delivery.evidence.envelope)
        _require(_evidence_validator.is_valid(envelope))
        semantic = _object(envelope.get("semantic"))
        source_observation = _object(semantic.get("observation"))
        projection = _object(envelope.get("surveillance_evidence"))
        measure = _object(semantic.get("measure"))
        applicability = _object(measure.get("applicability"))
        geography = _object(source_observation.get("geography"))
        temporal = _object(source_observation.get("temporal"))
        provenance = _object(semantic.get("provenance"))
        lineage = _object(semantic.get("lineage"))
        identity = delivery.evidence.identity
        release = _object(semantic.get("release"))
        _require(
            measure.get("id") == selected.semantic_measure_id
            and applicability.get("geography_grain") == "COUNTY"
            and applicability.get("temporal_semantics")
            == selected.semantic_temporal_semantics
            and geography.get("grain") == "COUNTY"
            and geography.get("county_fips") == observation.geography.geography_id
            and temporal.get("semantics") == selected.semantic_temporal_semantics
        )
        if selected.semantic_temporal_semantics == "PERIOD":
            _require(
                temporal.get("start") == observation.period_start.isoformat()
                and temporal.get("end") == observation.period_end.isoformat()
            )
        elif selected.semantic_temporal_semantics == "CUMULATIVE_THROUGH_DATE":
            _require(temporal.get("date") == observation.period_end.isoformat())
        elif selected.semantic_temporal_semantics == "POINT_IN_TIME":
            _require(
                observation.period_start == observation.period_end
                and temporal.get("date") == observation.period_end.isoformat()
            )
        else:
            _require(False)
        _require(
            identity == delivery.primary_identity
            and semantic.get("evidence_tier") == "REVIEWED_CONTRACT"
            and projection.get("evidence_tier") == "GOVERNED_MAPPING"
            and source_observation.get("id")
            == identity.observation_key
            == projection.get("observation_key")
            and source_observation.get("revision_id")
            == identity.revision_id
            == projection.get("revision_id")
            and source_observation.get("value_state")
            == projection.get("value_state")
            == observation.value_state.value
            and source_observation.get("value") == observation.value
            and _known(provenance.get("source_id")) == identity.source_id
            and _known(provenance.get("source_version_id")) == identity.source_version_id
            and release.get("id") == binding.release_id
            and release.get("bundle_sha256") == binding.bundle_sha256
            and lineage.get("release_id") == binding.release_id
            and lineage.get("measure_id") == selected.semantic_measure_id
            and lineage.get("source_version_id") == identity.source_version_id
        )
        evidence = _safe_model(SurveillanceEvidenceContext, projection)

    comparison = None
    if delivery.comparison is not None:
        item = delivery.comparison
        certified = item.producer_binding
        comparison_projection = _object(item.projection)
        _require(
            item.right == delivery.primary_identity
            and bool(item.left.observation_key and item.left.revision_id)
            and bool(item.left.source_version_id and item.right.source_version_id)
            and item.left_resource_key in {"cdc_lyme_qtbi_xd4i", "cdc_lyme_x5j9_wybp"}
            and item.right_resource_key in {"cdc_lyme_qtbi_xd4i", "cdc_lyme_x5j9_wybp"}
            and item.right.source_id == observation.lineage_source_id
            and bool(item.left_public_observation_id)
            and item.left == certified.left
            and item.right == certified.right
            and item.left_resource_key == certified.left_resource_key
            and item.right_resource_key == certified.right_resource_key
            and item.left_public_observation_id == certified.left_public_observation_id
            and item.jurisdiction_evidence_reference
            == certified.jurisdiction_evidence_reference
            and comparison_projection == _object(certified.projection)
        )
        comparison = _safe_model(
            MethodologyComparisonContext,
            {**comparison_projection, "left_observation_id": item.left_public_observation_id},
        )
        _require(
            comparison.comparison_state == "UNKNOWN"
            or (
                certified.left_case_category in {"Confirmed", "Probable"}
                and certified.right_case_category in {"Confirmed", "Probable"}
                and certified.jurisdiction_class != "UNKNOWN"
                and comparison.jurisdiction_class == certified.jurisdiction_class
                and bool(comparison.references)
            )
        )

    alignment = None
    if delivery.alignment is not None:
        alignment_item = delivery.alignment
        projection = _object(alignment_item.projection)
        _require(
            alignment_item.input == delivery.primary_identity
            and bool(alignment_item.input.source_version_id)
            and alignment_item.input.source_id == observation.lineage_source_id
            and projection.get("source_id") == alignment_item.input.source_id
            and projection.get("measure_id") == observation.measure_id
            and projection.get("method_id") == alignment_item.method_id
            and projection.get("method_version") == alignment_item.method_version
            and projection.get("observation_window") == alignment_item.observation_window
            and projection.get("target_window") == alignment_item.target_window
            and projection.get("decision_cutoff") == alignment_item.decision_cutoff
            and projection.get("reference_period") == alignment_item.reference_period
        )
        alignment = _safe_model(TemporalAlignmentContext, projection)
    return SurveillanceContext(
        evidence=evidence, methodology_comparison=comparison, temporal_alignment=alignment
    )
