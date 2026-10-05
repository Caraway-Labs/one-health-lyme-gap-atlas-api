"""API20 fixture experiment through the actual #19/#100 service boundaries."""

import json
import subprocess
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_ask_atlas_mixed_composition import _literature
from test_ask_atlas_orchestration import FakeTools

from lyme_gap_atlas_api.ask_atlas_evals import (
    Candidate,
    EvalCase,
    EvalDataset,
    Measurements,
    compare,
    evaluate,
    safe_case_key,
)
from lyme_gap_atlas_api.ask_atlas_mixed_service import MixedAssistant
from lyme_gap_atlas_api.ask_atlas_orchestration import (
    StructuredAssistant,
    StructuredAssistantRequest,
)

DATA = EvalDataset.model_validate_json(
    (Path(__file__).parent / "fixtures/ask_atlas_eval_v1.json").read_text()
)
COMMIT = subprocess.check_output(
    ["git", "-c", "safe.directory=*", "rev-parse", "HEAD"],
    cwd=Path(__file__).resolve().parents[1], text=True,
).strip()
BASE = Candidate(
    name="bounded-v1", config_version="bounded-v1", code_commit=COMMIT,
    provider="none", model="deterministic", prompt_version="none",
    tool_schema_version="ask-atlas-v1", structured_release="fixture-release-1",
    literature_corpus="fixture-corpus", literature_index="fixture-index",
    retrieval_version="fixture-retrieval-v1",
)
SECOND = replace(
    BASE, name="observation-outage-v1", config_version="observation-outage-v1",
    tool_failure_mode="observation_unavailable",
)


def _run(case: EvalCase, candidate: Candidate = BASE, capture=None):
    tools = FakeTools()
    if candidate.tool_failure_mode == "observation_unavailable" or case.fixture_tool_failure:
        tools.fail_on = "get_observations"
    class LiteratureFixture:
        def chat(self, request, request_id, network_identifier):
            response = _literature()
            if case.fixture_literature_state == "conflicting":
                response.evidence_state = "conflicting"
            return response

    response = MixedAssistant(StructuredAssistant(tools), LiteratureFixture()).ask(
        case.question, case.mode,
        StructuredAssistantRequest(question=case.question, context=case.context).context,
        "eval-request", "fixture-client",
    )
    if capture is not None:
        capture.extend(tools.calls)
    return response


def _evaluate_case(case: EvalCase, candidate: Candidate):
    invocations = []
    response = _run(case, candidate, invocations)
    return evaluate(
        case, response, candidate=candidate, dataset_version=DATA.dataset_version,
        tool_invocations=invocations,
    )


def test_versioned_two_candidate_experiment_and_fail_closed_gate(monkeypatch) -> None:
    DATA.validate_identity()
    provider = TracerProvider()
    monkeypatch.setattr(trace, "get_tracer", lambda name: provider.get_tracer(name))
    results = []
    for candidate in (BASE, SECOND):
        for case in DATA.cases:
            with trace.get_tracer(__name__).start_as_current_span("eval-case"):
                results.append(_evaluate_case(case, candidate))
    baseline = results[:len(DATA.cases)]
    outage = results[len(DATA.cases):]
    assert all(item.passed for item in baseline), [item.failures for item in baseline]
    assert any(not item.passed for item in outage)
    assert any("outcome" in item.failures for item in outage)
    summary = compare(DATA, results, (BASE, SECOND))
    assert summary["promotion_gate"] is False
    assert summary["candidates"]["bounded-v1"]["passed"] == len(DATA.cases)
    assert summary["candidates"]["observation-outage-v1"]["failed"]
    assert all(item.measurements.provider_cost_usd is None for item in results)

    broken = DATA.cases[0].model_copy(update={"expected_value": 99})
    invocations = []
    response = _run(broken, capture=invocations)
    with trace.get_tracer(__name__).start_as_current_span("eval-case"):
        failure = evaluate(
            broken, response, candidate=BASE, dataset_version=DATA.dataset_version,
            tool_invocations=invocations,
        )
    assert failure.failures == ("value",)
    assert compare(DATA, [failure, *results[1:]], (BASE, SECOND))["promotion_gate"] is False


def test_schema_rejects_duplicates_and_incomplete_comparison() -> None:
    duplicate = DATA.model_copy(update={"cases": [DATA.cases[0], DATA.cases[0]]})
    with pytest.raises(ValueError, match="duplicate"):
        duplicate.validate_identity()
    with pytest.raises(ValueError, match="complete"):
        compare(DATA, [], (BASE, SECOND))
    with pytest.raises(ValueError, match="distinct"):
        compare(DATA, [], (BASE, BASE))
    with pytest.raises(ValueError, match="commit"):
        replace(BASE, code_commit="fixture-commit")
    with pytest.raises(ValueError, match="explicit"):
        replace(BASE, provider="")
    with pytest.raises(ValueError, match="dataset_version"):
        EvalDataset.model_validate({"dataset_version": "mutable", "cases": [DATA.cases[0]]})
    assert safe_case_key("structured_zero") == safe_case_key("structured_zero")
    assert "structured_zero" not in safe_case_key("structured_zero")


def test_compare_rejects_wrong_versions_and_uncorrelated_results(monkeypatch) -> None:
    provider = TracerProvider()
    monkeypatch.setattr(trace, "get_tracer", lambda name: provider.get_tracer(name))
    with trace.get_tracer(__name__).start_as_current_span("eval-case"):
        results = [_evaluate_case(case, BASE) for case in DATA.cases]
        results += [_evaluate_case(case, SECOND) for case in DATA.cases]
    with pytest.raises(ValueError, match="version mismatch"):
        compare(DATA, [replace(results[0], dataset_version="other"), *results[1:]],
                (BASE, SECOND))
    with pytest.raises(ValueError, match="trace correlation"):
        compare(DATA, [replace(results[0], trace_id=None), *results[1:]], (BASE, SECOND))


def test_trace_correlation_and_private_result(monkeypatch) -> None:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda name: provider.get_tracer(name))
    with trace.get_tracer(__name__).start_as_current_span("eval-fixture") as span:
        result = evaluate(
            DATA.cases[0], _run(DATA.cases[0]), candidate=BASE,
            dataset_version=DATA.dataset_version,
            measurements=Measurements(latency_ms=1.5, tool_latency_ms=0.5),
        )
        assert result.trace_id == f"{span.get_span_context().trace_id:032x}"
    service_spans = [
        item for item in exporter.get_finished_spans()
        if item.name == "atlas.ask_atlas.structured"
    ]
    assert service_spans
    assert all(f"{item.context.trace_id:032x}" == result.trace_id for item in service_spans)
    payload = json.dumps(result, default=lambda value: value.__dict__)
    for secret in (DATA.cases[0].question, "Fixture surveillance source", "fixture-prov-zero"):
        assert secret not in payload
    assert result.measurements.input_tokens is None


def test_evaluator_rejects_plausibly_shaped_unsupported_claims_and_citations() -> None:
    structured_case = DATA.cases[0]
    structured = _run(structured_case)
    assert structured.structured is not None
    structured.structured.answer.claims[0].text = "County 08001 has 999 cases."
    failure = evaluate(
        structured_case, structured, candidate=BASE, dataset_version=DATA.dataset_version
    )
    assert "structured_claim_grounding" in failure.failures

    both_case = next(case for case in DATA.cases if case.case_id == "both_two_sources")
    for mutate, expected in (
        (lambda result: setattr(
            result.literature.claims[0], "text",
            "The study establishes that deer cause Lyme disease."
        ), "literature_claim_grounding"),
        (lambda result: setattr(
            result.literature.citations[0], "passage_ids", ["passage:99999:8"]
        ), "passage_provenance"),
        (lambda result: setattr(result.literature.citations[0], "pmid", "99999"),
         "citation_provenance"),
        (lambda result: setattr(result.literature.citations[0], "citation_id", "pmid:99999"),
         "citation_identity"),
        (lambda result: setattr(result.literature.claims[0], "citation_ids", []),
         "unsupported_literature_claim"),
    ):
        response = _run(both_case)
        assert response.literature is not None
        mutate(response)
        result = evaluate(
            both_case, response, candidate=BASE, dataset_version=DATA.dataset_version
        )
        assert expected in result.failures
    missing_literature = replace(_run(both_case), literature=None)
    failure = evaluate(
        both_case, missing_literature, candidate=BASE, dataset_version=DATA.dataset_version
    )
    assert "missing_literature" in failure.failures
    unverified = replace(_run(both_case), outcome="ANSWERED")
    failure = evaluate(
        both_case, unverified, candidate=BASE, dataset_version=DATA.dataset_version
    )
    assert "unverified_comparison" in failure.failures


@pytest.mark.parametrize(("mutation", "expected_failure"), [
    ("wrong_outcome", "outcome"),
    ("wrong_source", "source_routing"),
    ("wrong_tool", "tool_selection"),
    ("wrong_args", "tool_parameters"),
    ("wrong_state", "value_state"),
    ("wrong_release", "release_provenance"),
    ("missing_invocations", "tool_invocations_missing"),
    ("ungrounded_claim", "unsupported_structured_claim"),
    ("causal_claim", "causal_promotion"),
    ("promoted_tool_failure", "tool_failure_promoted"),
    ("failed_abstention", "failed_abstention"),
])
def test_objective_gates_fail_known_regressions(mutation, expected_failure) -> None:
    case = DATA.cases[0].model_copy(deep=True)
    invocations = []
    response = _run(case, capture=invocations)
    assert response.structured is not None
    if mutation == "wrong_outcome":
        case.expected_outcome = "INSUFFICIENT_EVIDENCE"
    elif mutation == "wrong_source":
        case.expected_sources = []
    elif mutation == "wrong_tool":
        case.expected_tools = ["get_evidence_metadata"]
    elif mutation == "wrong_args":
        invocations[0]["year"] = 2022
    elif mutation == "wrong_state":
        case.expected_value_state = "OBSERVED"
    elif mutation == "wrong_release":
        case.expected_release = "other-release"
    elif mutation == "missing_invocations":
        invocations = None
    elif mutation == "ungrounded_claim":
        response.structured.answer.claims[0].structured_refs = []
    elif mutation == "causal_claim":
        response.structured.answer.claims[0].text = "This causes Lyme disease."
    elif mutation == "promoted_tool_failure":
        response.structured.tool_evidence[0].status = "error"
    elif mutation == "failed_abstention":
        response = replace(response, outcome="INSUFFICIENT_EVIDENCE")
    result = evaluate(
        case, response, candidate=BASE, dataset_version=DATA.dataset_version,
        tool_invocations=invocations,
    )
    assert expected_failure in result.failures


def test_flat_structured_boundary_and_missing_release() -> None:
    case = DATA.cases[0]
    tools = FakeTools()
    flat = StructuredAssistant(tools).ask(
        StructuredAssistantRequest(question=case.question, context=case.context)
    )
    passed = evaluate(
        case, flat, candidate=BASE, dataset_version=DATA.dataset_version,
        tool_invocations=tools.calls,
    )
    assert passed.passed
    flat.answer.replay.release_id = None
    failed = evaluate(
        case, flat, candidate=BASE, dataset_version=DATA.dataset_version,
        tool_invocations=tools.calls,
    )
    assert "missing_release" in failed.failures


def test_exporter_and_eval_backend_outage_cannot_affect_request(monkeypatch) -> None:
    class FailingExporter(SpanExporter):
        def __init__(self) -> None:
            self.calls = 0

        def export(self, spans):
            self.calls += 1
            raise ConnectionError("Phoenix unavailable")

        def shutdown(self) -> None:
            pass

    exporter = FailingExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda name: provider.get_tracer(name))

    # StructuredAssistant emits spans before returning. A failed exporter must
    # not turn this governed zero observation into a request failure.
    response = _run(DATA.cases[0])
    assert response.outcome == "ANSWERED"
    assert exporter.calls >= 1

    def unavailable_backend(_result):
        raise ConnectionError("eval backend unavailable")

    # Optional experiment recording runs after the real service returns. The
    # request response is retained even when the recording backend fails.
    with suppress(ConnectionError):
        unavailable_backend(evaluate(
            DATA.cases[0], response, candidate=BASE, dataset_version=DATA.dataset_version
        ))
    assert response.structured and response.structured.answer.claims
