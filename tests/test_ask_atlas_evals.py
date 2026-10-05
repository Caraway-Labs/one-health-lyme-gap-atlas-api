"""API20 fixture experiment through the actual #19/#100 service boundaries."""

import json
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from test_ask_atlas_mixed_composition import _literature
from test_ask_atlas_orchestration import FakeTools

from lyme_gap_atlas_api.ask_atlas_evals import (
    Candidate,
    EvalCase,
    EvalDataset,
    Measurements,
    compare,
    evaluate,
)
from lyme_gap_atlas_api.ask_atlas_mixed_composition import compose_results
from lyme_gap_atlas_api.ask_atlas_orchestration import (
    StructuredAssistant,
    StructuredAssistantRequest,
)

DATA = EvalDataset.model_validate_json(
    (Path(__file__).parent / "fixtures/ask_atlas_eval_v1.json").read_text()
)
BASE = Candidate(
    name="bounded-v1", config_version="bounded-v1", code_commit="fixture-commit",
    provider="none", model="deterministic", prompt_version="none",
    tool_schema_version="ask-atlas-v1", structured_release="fixture-release-1",
    literature_corpus="fixture-corpus", literature_index="fixture-index",
    retrieval_version="fixture-retrieval-v1",
)
SECOND = replace(BASE, name="bounded-v1-repeat", config_version="bounded-v1-repeat")


def _run(case: EvalCase):
    structured = StructuredAssistant(FakeTools()).ask(
        StructuredAssistantRequest(question=case.question, context=case.context)
    )
    if case.mode == "Structured":
        return structured
    return compose_results(
        "Both", structured=structured, literature=_literature(),
        structured_requested=True, literature_requested=True,
    )


def test_versioned_two_candidate_experiment_and_fail_closed_gate() -> None:
    DATA.validate_identity()
    results = [
        evaluate(case, _run(case), candidate=candidate, dataset_version=DATA.dataset_version)
        for candidate in (BASE, SECOND) for case in DATA.cases
    ]
    assert all(item.passed for item in results), [item.failures for item in results]
    summary = compare(DATA, results, (BASE, SECOND))
    assert summary["promotion_gate"] is True
    assert summary["candidates"]["bounded-v1"]["passed"] == len(DATA.cases)
    assert all(item.measurements.provider_cost_usd is None for item in results)

    broken = DATA.cases[0].model_copy(update={"expected_value": 99})
    failure = evaluate(broken, _run(broken), candidate=BASE, dataset_version=DATA.dataset_version)
    assert failure.failures == ("value",)
    assert compare(DATA, [failure, *results[1:]], (BASE, SECOND))["promotion_gate"] is False


def test_schema_rejects_duplicates_and_incomplete_comparison() -> None:
    duplicate = DATA.model_copy(update={"cases": [DATA.cases[0], DATA.cases[0]]})
    with pytest.raises(ValueError, match="duplicate"):
        duplicate.validate_identity()
    with pytest.raises(ValueError, match="complete"):
        compare(DATA, [], (BASE, SECOND))


def test_trace_correlation_and_private_result() -> None:
    provider = TracerProvider()
    with provider.get_tracer(__name__).start_as_current_span("fixture") as span:
        result = evaluate(
            DATA.cases[0], _run(DATA.cases[0]), candidate=BASE,
            dataset_version=DATA.dataset_version,
            measurements=Measurements(latency_ms=1.5, tool_latency_ms=0.5),
        )
        assert result.trace_id == f"{span.get_span_context().trace_id:032x}"
    payload = json.dumps(result, default=lambda value: value.__dict__)
    for secret in (DATA.cases[0].question, "Fixture surveillance source", "fixture-prov-zero"):
        assert secret not in payload
    assert result.measurements.input_tokens is None


def test_eval_backend_failure_cannot_affect_request() -> None:
    response = _run(DATA.cases[0])

    def unavailable_backend(_result):
        raise ConnectionError("Phoenix unavailable")

    with suppress(ConnectionError):
        unavailable_backend(evaluate(
            DATA.cases[0], response, candidate=BASE, dataset_version=DATA.dataset_version
        ))
    assert response.answer.outcome == "ANSWERED"
    assert trace.get_current_span().get_span_context().trace_id == 0
