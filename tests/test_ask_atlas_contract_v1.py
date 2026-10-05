"""Executable specification assertions, without implementing API #18 tools."""

import json
from datetime import date
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
    if query.start_date is not None and query.end_date is not None and (
        (query.start_date.month, query.start_date.day) != (1, 1)
        or (query.end_date.month, query.end_date.day) != (12, 31)
    ):
        raise PublicQueryError("UNSUPPORTED_FILTER", "Annual ranges require whole years")


def assert_safety_case(case: dict[str, Any]) -> None:
    if case["context"].get("safety_class"):
        assert case["outcome"] == "SAFETY_REFUSAL"
        assert case["calls"] == case["sources"] == case["claim_refs"] == []
        assert not any(case[key] for key in (
            "claim_measure_ids", "claim_coverage_ids", "claim_literature_ids"
        ))


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
        assert set(case["claim_measure_ids"]) <= {evidence["measure"]["measure_id"]}
        if case["claim_coverage_ids"]:
            assert case.get("absent_geography_ids")
            expected = {
                f"coverage:fixture-release-1:case_count_floor_2023:county:{fips}:2023"
                for fips in case["absent_geography_ids"]
            }
            assert set(case["claim_coverage_ids"]) <= expected
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
            assert any(case[key] for key in (
                "claim_refs", "claim_measure_ids", "claim_coverage_ids", "claim_literature_ids"
            ))
        if case["cross_source_state"] is not None:
            assert case["mode"] == "Both"
            assert set(case["sources"]) == {"structured_atlas", "literature_evidence"}
        if case["id"] == "gap_absent":
            assert set(case["absent_geography_ids"]) == {"08005"}
            assert all(item.geography.geography_id != "08005" for item in admitted)
            assert case["claim_refs"] == []
        if case["id"] == "all_absent":
            assert not admitted and case["claim_coverage_ids"]
        if case["id"] == "measure_discovery":
            assert case["claim_measure_ids"] == [evidence["measure"]["measure_id"]]
        if case["id"] == "partial_annual_range":
            assert not case["calls"] and case["outcome"] == "UNSUPPORTED_REQUEST"
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
    partial = {**base, "start_date": "2023-07-01", "end_date": "2023-12-31"}
    partial.pop("year")
    calls.validate(partial)
    with pytest.raises(PublicQueryError, match="whole years"):
        validate_observation_call(partial)
    whole = {**partial, "start_date": "2023-01-01"}
    validate_observation_call(whole)
    assert date.fromisoformat(whole["start_date"]) <= date.fromisoformat(whole["end_date"])


def assert_answer_relationships(answer: dict[str, Any]) -> None:
    claims = answer["claims"]
    assert len({claim["claim_id"] for claim in claims}) == len(claims)
    observation_refs = answer["structured_evidence_refs"]
    measure_ids = {measure["measure_id"] for measure in answer["structured_measures"]}
    coverage_ids = {slot["coverage_id"] for slot in answer["structured_coverage"]}
    citations = {c["citation_id"]: c for c in answer["literature_citations"]}
    for measure in answer["structured_measures"]:
        canonical = Measure.model_validate(measure)
        assert canonical.release_version == answer["replay"]["release_id"]
    for slot in answer["structured_coverage"]:
        assert slot["release_id"] == answer["replay"]["release_id"]
        assert slot["measure_id"]
        assert (slot["state"] == "absent") == (slot["observation_id"] is None)
    structured_cited = False
    literature_cited = False
    for claim in claims:
        cited_structured = bool(
            claim["structured_refs"] or claim["measure_ids"] or claim["coverage_ids"]
        )
        cited_literature = bool(claim["literature_citation_ids"])
        assert cited_structured or cited_literature
        assert all(ref in observation_refs for ref in claim["structured_refs"])
        assert set(claim["measure_ids"]) <= measure_ids
        assert set(claim["coverage_ids"]) <= coverage_ids
        assert set(claim["literature_citation_ids"]) <= citations.keys()
        for citation_id in claim["literature_citation_ids"]:
            assert claim["claim_id"] in citations[citation_id]["claim_ids"]
        if cited_literature:
            assert not claim["claim_id"].startswith("atlas:")
        else:
            assert claim["claim_id"].startswith("atlas:")
        structured_cited |= cited_structured
        literature_cited |= cited_literature
    for citation in citations.values():
        KnowledgeCitation.model_validate(citation)
        assert citation["claim_ids"]
        for claim_id in citation["claim_ids"]:
            assert any(
                claim["claim_id"] == claim_id
                and citation["citation_id"] in claim["literature_citation_ids"]
                for claim in claims
            )
    assert ("structured_atlas" in answer["actual_sources_used"]) == structured_cited
    assert ("literature_evidence" in answer["actual_sources_used"]) == literature_cited
    literature_ran = answer["replay"]["literature_retrieval_configuration_version"] is not None
    assert (answer["literature_evidence_state"] is not None) == literature_ran
    if literature_cited:
        assert literature_ran
    freshness_ids = [item["observation_id"] for item in answer["freshness"]]
    assert len(freshness_ids) == len(set(freshness_ids))
    assert set(freshness_ids) == {ref["resource_id"] for ref in observation_refs}
    for item in answer["freshness"]:
        if item["state"] == "unknown":
            assert item["policy_id"] is None
        else:
            assert all(item[key] is not None for key in (
                "policy_id", "source_timestamp", "compared_at"
            ))
    if answer["outcome"] == "INSUFFICIENT_EVIDENCE" and (
        answer["cross_source_state"] == "insufficient_to_compare"
    ):
        assert claims and all(claim["role"] == "comparison_limitation" for claim in claims)
        assert set(answer["actual_sources_used"]) == {
            "structured_atlas", "literature_evidence"
        }
    elif answer["outcome"] != "ANSWERED":
        assert not claims and not answer["actual_sources_used"]
    else:
        assert claims and all(claim["role"] == "finding" for claim in claims)
    if answer["cross_source_state"] is not None:
        assert answer["requested_source_mode"] == "Both"
        assert structured_cited and literature_cited


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
                assert slot["release_id"] == example["release_id"]
                year = date.fromisoformat(slot["period_start"]).year
                expected_id = (
                    f"coverage:{slot['release_id']}:{slot['measure_id']}:"
                    f"{slot['geography']['geography_type']}:"
                    f"{slot['geography']['geography_id']}:{year}"
                )
                assert slot["coverage_id"] == expected_id
            for item in example["metadata"]:
                source = Source.model_validate(item["source"])
                assert source.release_version == example["release_id"]
                if item["methodology"] is not None:
                    method = Methodology.model_validate(item["methodology"])
                    assert method.release_version == example["release_id"]
        else:
            assert_answer_relationships(example)
    assert evidence["literature"]["support_quote"]
    assert any(
        item["kind"] == "tool_result" and item["status"] == "ok"
        and item["coverage"] and not item["observations"]
        and all(slot["state"] == "absent" for slot in item["coverage"])
        for item in examples["valid"]
    )
    for example in examples["invalid"]:
        assert not results.is_valid(example)


def test_adversarial_answer_identity_and_freshness_mutations() -> None:
    examples = read_json(FIXTURES / "ask_atlas_envelopes_v1.json")["valid"]
    mixed = next(item for item in examples if item.get("cross_source_state") == "discordant")
    broken_citation = json.loads(json.dumps(mixed))
    broken_citation["claims"][0]["literature_citation_ids"] = ["not-admitted"]
    with pytest.raises(AssertionError):
        assert_answer_relationships(broken_citation)
    missing_freshness = json.loads(json.dumps(mixed))
    missing_freshness["freshness"] = []
    with pytest.raises(AssertionError):
        assert_answer_relationships(missing_freshness)
    broken_claim_id = json.loads(json.dumps(mixed))
    broken_claim_id["claims"][1]["claim_id"] = "unknown-literature-claim"
    with pytest.raises(AssertionError):
        assert_answer_relationships(broken_claim_id)
    absent = next(item for item in examples if item.get("structured_coverage"))
    wrong_coverage = json.loads(json.dumps(absent))
    wrong_coverage["claims"][0]["coverage_ids"] = ["another-county"]
    with pytest.raises(AssertionError):
        assert_answer_relationships(wrong_coverage)
    discovery = next(item for item in examples if item.get("structured_measures"))
    wrong_measure = json.loads(json.dumps(discovery))
    wrong_measure["claims"][0]["measure_ids"] = ["unknown-measure"]
    with pytest.raises(AssertionError):
        assert_answer_relationships(wrong_measure)
    literature_only = next(
        item for item in examples if item.get("requested_source_mode") == "Literature"
    )
    wrong_literature = json.loads(json.dumps(literature_only))
    wrong_literature["literature_citations"][0]["claim_ids"] = ["not-a-claim"]
    with pytest.raises(AssertionError):
        assert_answer_relationships(wrong_literature)
    incomparable = next(
        item for item in examples if item.get("cross_source_state") == "insufficient_to_compare"
    )
    false_finding = json.loads(json.dumps(incomparable))
    false_finding["claims"][0]["role"] = "finding"
    with pytest.raises(AssertionError):
        assert_answer_relationships(false_finding)
