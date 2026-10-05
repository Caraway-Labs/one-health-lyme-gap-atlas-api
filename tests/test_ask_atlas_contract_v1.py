"""Machine checked specification fixtures; production tools belong to API #18."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from lyme_gap_atlas_api.public_contract import ValueState


def test_golden_contract_is_versioned_and_deterministic() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "ask_atlas_contract_v1.json").read_text()
    )
    assert fixture["contract_version"] == "ask-atlas-v1"
    assert set(fixture["source_modes"]) == {"Literature", "Structured", "Both"}
    assert len(fixture["cases"]) >= 20
    assert len({case["id"] for case in fixture["cases"]}) == len(fixture["cases"])
    for case in fixture["cases"]:
        assert case["question"]
        assert case["mode"] in fixture["source_modes"]
        assert case["outcome"] in fixture["outcomes"]
        assert set(case["tools"]) <= set(fixture["tool_ids"])
        assert set(case["sources"]) <= {"structured_atlas", "literature_evidence"}
        if case["mode"] == "Literature":
            assert "structured_atlas" not in case["sources"]
        if case["mode"] == "Structured":
            assert "literature_evidence" not in case["sources"]
        if "cross_source_state" in case:
            assert case["mode"] == "Both"
            assert set(case["sources"]) == {"structured_atlas", "literature_evidence"}
            assert case["cross_source_state"] in {
                "aligned", "partially_aligned", "discordant", "insufficient_to_compare"
            }
    assert {state.value for state in ValueState} == {
        "OBSERVED", "ZERO", "MISSING", "SUPPRESSED", "UNAVAILABLE",
        "NO_COUNTY_LINKED_RECORD",
    }


def test_tool_schema_accepts_bounded_calls_and_rejects_unbounded_queries() -> None:
    schema = json.loads(
        (Path(__file__).parent.parent / "docs" / "ask-atlas-tools-v1.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    valid_calls = [
        {"tool": "find_measures", "search_text": "Lyme", "page_size": 20},
        {"tool": "get_observations", "measure_id": "case_count_floor_2023", "geographies":
         [{"geography_type": "county", "geography_id": "08001"}], "year": 2023},
        {"tool": "get_evidence_metadata", "evidence_refs":
         [{"resource_type": "observation", "resource_id": "example"}]},
    ]
    for call in valid_calls:
        validator.validate(call)
    invalid_calls = [
        {"tool": "get_observations", "measure_id": "x", "geographies": [], "year": 2023},
        {"tool": "get_observations", "measure_id": "x", "geographies":
         [{"geography_type": "county", "geography_id": "08001"}],
         "year": 2023, "sql": "SELECT *"},
        {"tool": "find_measures", "search_text": "x", "page_size": 21},
        {"tool": "get_evidence_metadata", "evidence_refs": []},
    ]
    for call in invalid_calls:
        assert not validator.is_valid(call)
