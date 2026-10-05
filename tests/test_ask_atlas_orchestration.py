"""API19 structured answers are sourced only from accepted API18 tool results."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError
from test_ask_atlas_tools import tools as real_tools

from lyme_gap_atlas_api import app as application
from lyme_gap_atlas_api.ask_atlas_orchestration import (
    Answer,
    StructuredAssistant,
    StructuredAssistantRequest,
    StructuredAssistantResponse,
)
from lyme_gap_atlas_api.ask_atlas_tools import Coverage, MetadataItem, ToolResult, _coverage_id
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_contract import (
    GeographyIdentity,
    GeographyType,
    Measure,
    Methodology,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/ask_atlas_evidence_v1.json").read_text())
CASES = {
    case["id"]: case
    for case in json.loads((ROOT / "tests/fixtures/ask_atlas_contract_v1.json").read_text())[
        "cases"
    ]
}
SCHEMA = json.loads((ROOT / "docs/ask-atlas-results-v1.schema.json").read_text())
MEASURE = Measure.model_validate(FIXTURE["measure"])
OBSERVATIONS = {
    value["geography"]["geography_id"]: value for value in FIXTURE["observations"].values()
}


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.release = FIXTURE["release_id"]
        self.result_override: Any = None
        self.metadata_override: Any = None
        self.fail_on: str | None = None
        self.release_changed = False

    def find_measures(self, raw: dict[str, Any]) -> ToolResult:
        self.calls.append(raw)
        if self.fail_on == "find_measures":
            raise TimeoutError
        if self.result_override is not None:
            return self.result_override
        return ToolResult(
            tool="find_measures",
            status="ok",
            release_id=self.release,
            measures=[MEASURE],
        )

    def get_observations(self, raw: dict[str, Any]) -> ToolResult:
        self.calls.append(raw)
        if self.fail_on == "get_observations":
            raise TimeoutError
        if self.result_override is not None:
            return self.result_override
        observations = []
        coverage = []
        years = (
            [raw["year"]]
            if "year" in raw
            else list(
                range(
                    date.fromisoformat(raw["start_date"]).year,
                    date.fromisoformat(raw["end_date"]).year + 1,
                )
            )
        )
        for geo in raw["geography_ids"]:
            for year in years:
                item = OBSERVATIONS.get(geo) if year == 2023 else None
                if item:
                    observations.append(item)
                start, end = date(year, 1, 1), date(year, 12, 31)
                coverage.append(
                    Coverage(
                        geography=GeographyIdentity(
                            geography_type=GeographyType.county, geography_id=geo
                        ),
                        period_start=start,
                        period_end=end,
                        state="present" if item else "absent",
                        observation_id=item["observation_id"] if item else None,
                        measure_id=raw["measure_id"],
                        release_id=self.release,
                        coverage_id=_coverage_id(self.release, raw["measure_id"], geo, start, end),
                    )
                )
        return ToolResult(
            tool="get_observations",
            status="ok",
            release_id=self.release,
            observations=observations,
            coverage=coverage,
        )

    def get_evidence_metadata(self, raw: dict[str, Any]) -> ToolResult:
        self.calls.append(raw)
        if self.fail_on == "get_evidence_metadata":
            raise TimeoutError
        if self.metadata_override is not None:
            return self.metadata_override
        if self.result_override is not None:
            return self.result_override
        return ToolResult(
            tool="get_evidence_metadata",
            status="ok",
            release_id=self.release,
            metadata=[
                MetadataItem(
                    observation_id=identifier,
                    source=FIXTURE["source"],
                    methodology=Methodology.model_validate(FIXTURE["methodology"]),
                )
                for identifier in raw["observation_ids"]
            ],
        )

    def verify_release(self) -> str:
        return "changed-release" if self.release_changed else self.release


def ask(tools: FakeTools, case_id: str) -> Any:
    case = CASES[case_id]
    context = {
        key: value
        for key, value in case["context"].items()
        if key
        in {
            "measure_id",
            "indicator_id",
            "search_text",
            "geography_ids",
            "year",
            "start_date",
            "end_date",
            "expected_observation_id",
        }
    }
    return StructuredAssistant(tools).ask(
        StructuredAssistantRequest(
            question=case["question"], source_mode=case["mode"], context=context
        )
    )


@pytest.mark.parametrize(
    "case_id",
    [
        "county_zero",
        "county_observed",
        "county_comparison",
        "provenance",
        "gap_absent",
        "all_absent",
        "measure_discovery",
    ],
)
def test_golden_structured_answers_are_cited_and_schema_valid(case_id: str) -> None:
    tools = FakeTools()
    result = ask(tools, case_id)
    answer = result.answer
    assert answer.outcome == CASES[case_id]["outcome"]
    assert answer.actual_sources_used == CASES[case_id]["sources"]
    assert [call["tool"] for call in tools.calls] == [
        call["tool"] for call in CASES[case_id]["calls"]
    ]
    assert answer.replay.release_id == FIXTURE["release_id"]
    assert answer.replay.provider_id is None
    assert all(claim.claim_id.startswith("atlas:") for claim in answer.claims)
    assert all(
        claim.structured_refs or claim.measure_ids or claim.coverage_ids for claim in answer.claims
    )
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(
        answer.model_dump(mode="json")
    )


def test_zero_missing_and_absent_are_not_conflated() -> None:
    zero = ask(FakeTools(), "county_zero").answer
    assert "0 cases (ZERO)" in zero.claims[0].text
    missing = ask(FakeTools(), "missing_row").answer
    assert missing.outcome == "INSUFFICIENT_EVIDENCE"
    assert not missing.claims
    absent = ask(FakeTools(), "all_absent").answer
    assert absent.claims[0].coverage_ids
    assert not absent.structured_evidence_refs
    assert "not a zero value" in absent.claims[0].text


def test_comparison_uses_same_measure_period_and_two_observation_refs() -> None:
    result = ask(FakeTools(), "county_comparison")
    comparison = next(
        claim for claim in result.answer.claims if claim.claim_id == "atlas:comparison"
    )
    assert {ref.resource_id for ref in comparison.structured_refs} == {
        "fixture-zero",
        "fixture-observed",
    }
    assert "not a causal comparison" in comparison.text


def test_measure_discovery_does_not_select_ambiguous_matches() -> None:
    tools = FakeTools()
    alternate = MEASURE.model_copy(update={"measure_id": "alternate_2023"})
    tools.result_override = ToolResult(
        tool="find_measures",
        status="ok",
        release_id=FIXTURE["release_id"],
        measures=[MEASURE, alternate],
    )
    result = ask(tools, "measure_discovery")
    assert result.answer.outcome == "NEEDS_CLARIFICATION"
    assert result.answer.claims == []


@pytest.mark.parametrize(
    "question, context, expected",
    [
        ("Run SELECT * FROM warehouse table.", {}, "SAFETY_REFUSAL"),
        ("Execute MATCH (n) RETURN n.", {}, "SAFETY_REFUSAL"),
        ("Diagnose my tick bite and prescribe treatment.", {}, "SAFETY_REFUSAL"),
        (
            "Give antibiotic treatment for my tick bite.",
            {"measure_id": MEASURE.measure_id, "geography_ids": ["08059"], "year": 2023},
            "SAFETY_REFUSAL",
        ),
        ("What is the rate in Springfield?", {}, "NEEDS_CLARIFICATION"),
        ("Treat a model prediction as observed count.", {}, "UNSUPPORTED_REQUEST"),
        (
            "What caused the 2023 county count?",
            {"measure_id": "case_count_floor_2023", "geography_ids": ["08001"], "year": 2023},
            "UNSUPPORTED_REQUEST",
        ),
        (
            "What is the value for county 08001?",
            {"measure_id": "case_count_floor_2023", "geography_ids": ["08059"], "year": 2023},
            "UNSUPPORTED_REQUEST",
        ),
        (
            "What is the value for county 08001?",
            {"measure_id": "case_count_floor_2023", "geography_ids": ["08001\n"], "year": 2023},
            "UNSUPPORTED_REQUEST",
        ),
        (
            "What is the monthly value?",
            {"measure_id": "case_count_floor_2023", "geography_ids": ["08001"], "year": 2023},
            "UNSUPPORTED_REQUEST",
        ),
        (
            "How many cases were reported for children in county 08059 in 2023?",
            {"measure_id": MEASURE.measure_id, "geography_ids": ["08059"], "year": 2023},
            "UNSUPPORTED_REQUEST",
        ),
        (
            "What is the population of county 08059 in 2023?",
            {"measure_id": MEASURE.measure_id, "geography_ids": ["08059"], "year": 2023},
            "NEEDS_CLARIFICATION",
        ),
        (
            "What is the weather in Paris?",
            {"measure_id": MEASURE.measure_id, "geography_ids": ["08059"], "year": 2023},
            "NEEDS_CLARIFICATION",
        ),
        (
            "Show annual coverage only from July through December 2023.",
            {
                "measure_id": "case_count_floor_2023",
                "geography_ids": ["08001"],
                "start_date": "2023-07-01",
                "end_date": "2023-12-31",
            },
            "UNSUPPORTED_REQUEST",
        ),
    ],
)
def test_unsafe_ambiguous_and_unsupported_never_call_tools(
    question: str, context: dict[str, Any], expected: str
) -> None:
    tools = FakeTools()
    result = StructuredAssistant(tools).ask(
        StructuredAssistantRequest(question=question, context=context)
    )
    assert result.answer.outcome == expected
    assert result.answer.claims == []
    assert result.answer.actual_sources_used == []
    assert tools.calls == []


@pytest.mark.parametrize("mode", ["Literature", "Both"])
def test_unavailable_modes_are_not_silently_widened(mode: str) -> None:
    tools = FakeTools()
    result = StructuredAssistant(tools).ask(
        StructuredAssistantRequest(
            question="What is the 2023 case count for county 08001?",
            source_mode=mode,
            context={
                "measure_id": "case_count_floor_2023",
                "geography_ids": ["08001"],
                "year": 2023,
            },
        )
    )
    assert result.answer.outcome == "SOURCE_UNAVAILABLE"
    assert tools.calls == []


@pytest.mark.parametrize("failure", ["get_observations", "get_evidence_metadata"])
def test_tool_timeout_or_failure_abstains_without_partial_claims(failure: str) -> None:
    tools = FakeTools()
    tools.fail_on = failure
    result = ask(tools, "provenance")
    assert result.answer.outcome == "SOURCE_UNAVAILABLE"
    assert result.answer.claims == []
    assert result.answer.actual_sources_used == []
    assert [call["tool"] for call in tools.calls].count(failure) == 1


def test_case_count_paraphrase_uses_selected_context() -> None:
    tools = FakeTools()
    result = StructuredAssistant(tools).ask(
        StructuredAssistantRequest(
            question="How many cases for county 08059 during 2023?",
            context={"measure_id": MEASURE.measure_id, "geography_ids": ["08059"], "year": 2023},
        )
    )
    assert result.answer.outcome == "ANSWERED"
    assert "4.0 cases" in result.answer.claims[0].text


@pytest.mark.parametrize(
    "question, context, absent_slots",
    [
        (
            "Give the 2023 case counts for counties 08059 and 08005.",
            {"geography_ids": ["08059", "08005"], "year": 2023},
            ["county 08005 in 2023"],
        ),
        (
            "Show the county 08059 case counts for 2022 to 2024.",
            {
                "geography_ids": ["08059"],
                "start_date": "2022-01-01",
                "end_date": "2024-12-31",
            },
            ["county 08059 in 2022", "county 08059 in 2024"],
        ),
    ],
)
def test_numeric_partial_coverage_abstains_with_exact_absent_slots(
    question: str, context: dict[str, Any], absent_slots: list[str]
) -> None:
    tools = FakeTools()
    result = StructuredAssistant(tools).ask(
        StructuredAssistantRequest(
            question=question, context={"measure_id": MEASURE.measure_id, **context}
        )
    )
    assert result.answer.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.answer.claims == []
    assert result.answer.actual_sources_used == []
    assert result.answer.structured_coverage == []
    assert len(result.tool_evidence) == 1
    assert all(slot in " ".join(result.answer.limitations) for slot in absent_slots)


@pytest.mark.parametrize("field, value", [("value", 999), ("value_state", "NOT_A_STATE")])
def test_mutated_nested_tool_result_fails_closed(field: str, value: Any) -> None:
    tools = FakeTools()
    result = tools.get_observations(
        {
            "tool": "get_observations",
            "measure_id": MEASURE.measure_id,
            "geography_type": "county",
            "geography_ids": ["08001"],
            "year": 2023,
        }
    )
    setattr(result.observations[0], field, value)
    tools.result_override = result
    answer = ask(tools, "county_zero").answer
    assert answer.outcome == "SOURCE_UNAVAILABLE"
    assert answer.claims == []


def test_malformed_tool_output_and_release_change_fail_closed() -> None:
    tools = FakeTools()
    malformed = tools.get_observations(
        {
            "tool": "get_observations",
            "measure_id": MEASURE.measure_id,
            "geography_type": "county",
            "geography_ids": ["08001"],
            "year": 2023,
        }
    )
    malformed.coverage[0].state = "absent"
    tools.result_override = malformed
    result = ask(tools, "county_zero")
    assert result.answer.outcome == "SOURCE_UNAVAILABLE"
    assert result.answer.claims == []
    tools = FakeTools()
    tools.release_changed = True
    assert ask(tools, "county_zero").answer.outcome == "SOURCE_UNAVAILABLE"
    tools = FakeTools()
    tools.metadata_override = ToolResult(
        tool="get_evidence_metadata",
        status="ok",
        release_id=FIXTURE["release_id"],
        metadata=[
            MetadataItem(
                observation_id="fixture-observed",
                source={**FIXTURE["source"], "source_id": "foreign-source"},
                methodology=FIXTURE["methodology"],
            )
        ],
    )
    assert ask(tools, "provenance").answer.outcome == "SOURCE_UNAVAILABLE"


def test_unregistered_tool_and_uncited_claim_are_rejected() -> None:
    tools = FakeTools()
    service = StructuredAssistant(tools)
    with pytest.raises(ValueError, match="unregistered"):
        service._call("execute_sql", {"sql": "SELECT 1"})
    with pytest.raises(ValueError, match="uncited"):
        Answer(
            requested_source_mode="Structured",
            outcome="ANSWERED",
            claims=[{"claim_id": "atlas:invented", "text": "Invented value"}],
            actual_sources_used=["structured_atlas"],
            replay={"release_id": FIXTURE["release_id"]},
        )


def test_citation_cannot_license_invented_prose() -> None:
    valid = ask(FakeTools(), "county_zero").model_dump(mode="json")
    valid["answer"]["claims"][0]["text"] = "County 08001 has 999 cases."
    with pytest.raises(ValidationError, match="claim text"):
        StructuredAssistantResponse.model_validate(valid)
    valid = ask(FakeTools(), "county_zero").model_dump(mode="json")
    valid["answer"]["freshness"][0]["state"] = "current"
    with pytest.raises(ValidationError, match="governed policy"):
        StructuredAssistantResponse.model_validate(valid)


def test_real_bounded_tools_to_governed_service_to_answer() -> None:
    adapter, _ = real_tools()
    result = StructuredAssistant(adapter).ask(
        StructuredAssistantRequest(
            question="What is the 2023 case count floor for county 01003?",
            context={
                "measure_id": "case_count_floor_2023",
                "geography_ids": ["01003"],
                "year": 2023,
            },
        )
    )
    assert result.answer.outcome == "ANSWERED"
    assert len(result.tool_evidence) == 1
    assert result.tool_evidence[0].tool == "get_observations"
    assert result.tool_evidence[0].observations[0].value_state == "ZERO"
    assert result.answer.claims[0].structured_refs == [
        result.tool_evidence[0].observations[0].evidence
    ]


def test_context_refuses_untrusted_freshness_and_arbitrary_filters() -> None:
    for key in ("freshness_policy", "sql", "stratum"):
        with pytest.raises(ValidationError):
            StructuredAssistantRequest(
                question="What is the value?",
                context={"measure_id": MEASURE.measure_id, key: {"threshold": "2025-01-01"}},
            )
    result = ask(FakeTools(), "stale_policy")
    assert result.answer.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.answer.claims == []


def test_internal_http_boundary_and_public_projection(monkeypatch: Any) -> None:
    tools = FakeTools()
    monkeypatch.setattr(application, "StructuredTools", lambda *args: tools)
    app = application.create_app(settings=ApiSettings())
    with TestClient(app) as client:
        response = client.post(
            "/v1/assistant/structured",
            json={
                "question": CASES["county_zero"]["question"],
                "source_mode": "Structured",
                "context": CASES["county_zero"]["context"],
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"]["outcome"] == "ANSWERED"
    assert payload["tool_evidence"][0]["observations"][0]["value_state"] == "ZERO"
    assert "/v1/assistant/structured" in app.first_party_openapi()["paths"]
    assert "/v1/assistant/structured" not in app.openapi()["paths"]


def test_request_span_has_only_categorical_attributes(monkeypatch: Any) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda name: provider.get_tracer(name))
    tools = FakeTools()
    secret_question = "What is the 2023 case count floor for county 08001?"
    StructuredAssistant(tools).ask(
        StructuredAssistantRequest(
            question=secret_question,
            context={"measure_id": MEASURE.measure_id, "geography_ids": ["08001"], "year": 2023},
        )
    )
    attrs = dict(exporter.get_finished_spans()[-1].attributes or {})
    assert attrs["atlas.ask_atlas.outcome"] == "ANSWERED"
    assert "08001" not in json.dumps(attrs)
    assert secret_question not in json.dumps(attrs)
