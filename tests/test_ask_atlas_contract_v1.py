"""Executable specification assertions, without implementing API #18 tools."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from lyme_gap_atlas_api.models import KnowledgeCitation
from lyme_gap_atlas_api.public_contract import (
    GeographyIdentity,
    Measure,
    Methodology,
    Observation,
    ObservationQuery,
    PublicQueryError,
    Source,
    ValueState,
)

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validator(name: str) -> Draft202012Validator:
    schema = read_json(ROOT / "docs" / name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_observation_call(call: dict[str, Any]) -> None:
    """The schema is followed by the canonical semantic bound, not substituted for it."""
    for geography_id in call["geography_ids"]:
        GeographyIdentity(geography_type=call["geography_type"], geography_id=geography_id)
    query = ObservationQuery(
        measure_id=call["measure_id"],
        geography_type=call["geography_type"],
        geography_id=call["geography_ids"],
        year=call.get("year"),
        start_date=call.get("start_date"),
        end_date=call.get("end_date"),
    )
    query.validate_bounds(ceiling=200, annual=True)


def assert_safety_case(case: dict[str, Any]) -> None:
    if case["context"].get("safety_class"):
        assert case["outcome"] == "SAFETY_REFUSAL"
        assert case["calls"] == case["sources"] == case["claim_refs"] == []


def test_fixture_canonical_evidence_and_concrete_case_invariants() -> None:
    evidence = read_json(FIXTURES / "ask_atlas_evidence_v1.json")
    fixture = read_json(FIXTURES / "ask_atlas_contract_v1.json")
    calls = validator("ask-atlas-tools-v1.schema.json")
    assert fixture["contract_version"] == "ask-atlas-v1"
    assert len(fixture["cases"]) >= 20
    assert len({case["id"] for case in fixture["cases"]}) == len(fixture["cases"])
    Measure.model_validate(evidence["measure"])
    Source.model_validate(evidence["source"])
    Methodology.model_validate(evidence["methodology"])
    KnowledgeCitation.model_validate(evidence["literature"]["citation"])
    observations = {
        key: Observation.model_validate(value)
        for key, value in evidence["observations"].items()
    }
    assert {state.value for state in ValueState} == {
        "OBSERVED", "ZERO", "MISSING", "SUPPRESSED", "UNAVAILABLE",
        "NO_COUNTY_LINKED_RECORD",
    }
    assert observations["zero"].value_state == ValueState.ZERO
    assert observations["missing"].value is None
    for case in fixture["cases"]:
        assert case["question"] and case["mode"] in {"Literature", "Structured", "Both"}
        assert case["outcome"] in {
            "ANSWERED", "NEEDS_CLARIFICATION", "INSUFFICIENT_EVIDENCE",
            "SOURCE_UNAVAILABLE", "UNSUPPORTED_REQUEST", "SAFETY_REFUSAL",
        }
        assert set(case["sources"]) <= {"structured_atlas", "literature_evidence"}
        for call in case["calls"]:
            if call["tool"] == "literature_answer":
                assert call == {"tool": "literature_answer", "existing_contract": "api-14"}
            else:
                calls.validate(call)
                if call["tool"] == "get_observations":
                    validate_observation_call(call)
        admitted = [observations[key] for key in case["observation_keys"]]
        assert set(case["claim_refs"]) <= {item.observation_id for item in admitted}
        assert_safety_case(case)
        if case["outcome"] in {"SAFETY_REFUSAL", "NEEDS_CLARIFICATION"}:
            assert not case["calls"] and not case["sources"]
        if case["outcome"] == "SOURCE_UNAVAILABLE":
            assert not case["sources"] and not case["claim_refs"]
        if case["mode"] == "Literature":
            assert "structured_atlas" not in case["sources"]
        if case["mode"] == "Structured":
            assert "literature_evidence" not in case["sources"]
        if case["sources"]:
            assert case["claim_refs"] or case["context"].get("literature_citation_id")
        if case["cross_source_state"] is not None:
            assert case["mode"] == "Both"
            assert set(case["sources"]) == {"structured_atlas", "literature_evidence"}
        if case["id"] == "gap_absent":
            assert set(case["absent_geography_ids"]) == {"08005"}
            assert all(item.geography.geography_id != "08005" for item in admitted)
        if case["id"] == "provenance":
            assert [call["tool"] for call in case["calls"]] == [
                "get_observations", "get_evidence_metadata"
            ]
            assert case["context"]["expected_observation_id"] == admitted[0].observation_id
        if case["id"] == "missing_row":
            assert admitted[0].value_state == ValueState.MISSING
            assert case["outcome"] == "INSUFFICIENT_EVIDENCE"
        if case["id"] == "stale_policy":
            policy = case["context"]["freshness_policy"]
            assert policy["source_timestamp"] < policy["threshold"]
            assert case["freshness"] == "stale"
        if case["id"] == "both_conflict":
            assert case["context"]["comparison"] == "opposed_scoped_claims"
            assert case["cross_source_state"] == "discordant"


def test_safety_assertion_detects_mutated_clinical_fixture() -> None:
    fixture = read_json(FIXTURES / "ask_atlas_contract_v1.json")
    clinical = next(case for case in fixture["cases"] if case["id"] == "clinical_refusal")
    altered = {**clinical, "outcome": "ANSWERED", "calls": [{"tool": "literature_answer"}]}
    with pytest.raises(AssertionError):
        assert_safety_case(altered)


def test_typed_input_rejects_adversarial_calls_and_canonical_invalid_bounds() -> None:
    calls = validator("ask-atlas-tools-v1.schema.json")
    valid = [
        {"tool": "find_measures", "search_text": "Lyme", "page_size": 20},
        {"tool": "get_evidence_metadata", "observation_ids": ["fixture-zero"]},
    ]
    for call in valid:
        calls.validate(call)
    invalid = [
        {"tool": "find_measures", "search_text": "x", "indicator_id": "y", "page_size": 20},
        {"tool": "find_measures", "search_text": "x", "page_size": 21},
        {"tool": "get_evidence_metadata", "observation_ids": []},
        {"tool": "get_observations", "measure_id": "x", "geography_type": "county",
         "geography_ids": ["08001"], "year": 2023, "sql": "SELECT *"},
        {"tool": "get_observations", "measure_id": "x", "geography_type": "county",
         "geography_ids": ["08001"], "start_date": "not-a-date", "end_date": "2023-12-31"},
    ]
    for call in invalid:
        assert not calls.is_valid(call)
    base = {"tool": "get_observations", "measure_id": "x", "geography_type": "county",
            "geography_ids": ["08001"], "year": 2023}
    calls.validate(base)
    validate_observation_call(base)
    newline = {**base, "geography_ids": ["08001\n"]}
    with pytest.raises(ValueError):
        validate_observation_call(newline)
    reversed_range = {**base, "year": None, "start_date": "2024-01-01",
                      "end_date": "2023-01-01"}
    reversed_range.pop("year")
    calls.validate(reversed_range)
    with pytest.raises(PublicQueryError, match="end_date"):
        validate_observation_call(reversed_range)
    too_broad = {**base, "geography_ids": [f"{index:05d}" for index in range(20)],
                 "start_date": "2000-01-01", "end_date": "2010-12-31"}
    too_broad.pop("year")
    calls.validate(too_broad)
    with pytest.raises(PublicQueryError, match="Narrow"):
        validate_observation_call(too_broad)


def test_positive_and_negative_result_answer_envelopes() -> None:
    evidence = read_json(FIXTURES / "ask_atlas_evidence_v1.json")
    examples = read_json(FIXTURES / "ask_atlas_envelopes_v1.json")
    results = validator("ask-atlas-results-v1.schema.json")
    for example in examples["valid"]:
        results.validate(example)
        if example["kind"] == "tool_result":
            assert (example["status"] == "ok") == (example["error_code"] is None)
            if example["status"] == "error":
                fields = ("measures", "observations", "coverage", "metadata")
                assert not any(example[key] for key in fields)
            for item in example["measures"]:
                Measure.model_validate(item)
            for item in example["observations"]:
                observation = Observation.model_validate(item)
                assert observation.release_id == example["release_id"]
                assert observation.evidence.resource_id == observation.observation_id
                assert observation.evidence.release_id == example["release_id"]
                matches = [
                    slot for slot in example["coverage"]
                    if slot["observation_id"] == observation.observation_id
                ]
                assert len(matches) == 1 and matches[0]["state"] == "present"
            assert len({
                (slot["geography"]["geography_id"], slot["period_start"], slot["period_end"])
                for slot in example["coverage"]
            }) == len(example["coverage"])
            for slot in example["coverage"]:
                assert (slot["state"] == "absent") == (slot["observation_id"] is None)
            for item in example["metadata"]:
                source = Source.model_validate(item["source"])
                assert source.release_version == example["release_id"]
                if item["methodology"] is not None:
                    method = Methodology.model_validate(item["methodology"])
                    assert method.release_version == example["release_id"]
        else:
            assert example["actual_sources_used"] or example["outcome"] != "ANSWERED"
            if example["outcome"] != "ANSWERED":
                assert not example["claims"] or (
                    example["cross_source_state"] == "insufficient_to_compare"
                )
            for claim in example["claims"]:
                assert claim["structured_refs"] or claim["literature_citation_ids"]
                assert all(
                    ref in example["structured_evidence_refs"] for ref in claim["structured_refs"]
                )
            for citation in example["literature_citations"]:
                KnowledgeCitation.model_validate(citation)
            if example["cross_source_state"] is not None:
                assert example["requested_source_mode"] == "Both"
                assert set(example["actual_sources_used"]) == {
                    "structured_atlas", "literature_evidence"
                }
                assert all(
                    any(c["citation_id"] == citation_id for c in example["literature_citations"])
                    for citation_id in claim["literature_citation_ids"]
                )
    assert evidence["literature"]["support_quote"]
    for example in examples["invalid"]:
        assert not results.is_valid(example)
