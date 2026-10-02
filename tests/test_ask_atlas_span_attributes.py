"""The stored service boundary is singular, bounded, and never copies user content."""
import json
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_knowledge_chat_diagnostics import (
    EVIDENCE,
    SECRET,
    Answerer,
    Retriever,
    candidate,
)

from lyme_gap_atlas_api.knowledge_chat import KnowledgeChatService, _completion_attributes
from lyme_gap_atlas_api.models import KnowledgeChatRequest


@pytest.fixture
def spans(monkeypatch: Any) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda name: provider.get_tracer(name))
    return exporter


@pytest.mark.parametrize("evidence,results,outcome,category", [
    ([], [], "no_evidence", "intentional_abstention"),
    (RuntimeError(SECRET), [], "retrieval_dependency_unavailable", "failure"),
    (EVIDENCE, [candidate()], "answered", "answered"),
])
def test_service_completion_and_parentage(
    spans: InMemorySpanExporter, evidence: Any, results: Any, outcome: str, category: str,
) -> None:
    service = KnowledgeChatService(Retriever(evidence), Answerer(results), None, SECRET)
    service.chat(KnowledgeChatRequest(message=SECRET), "safe-correlation", SECRET)
    finished = spans.get_finished_spans()
    boundaries = [s for s in finished if s.name == "knowledge_chat.service"]
    assert len(boundaries) == 1
    boundary = boundaries[0]
    attrs = dict(boundary.attributes or {})
    assert attrs["atlas.ask_atlas.outcome"] == outcome
    assert attrs["atlas.ask_atlas.outcome_class"] == category
    assert attrs["request.id"] == "safe-correlation"
    assert "atlas.ask_atlas.model_id" not in attrs
    assert SECRET not in json.dumps([dict(s.attributes or {}) for s in finished])
    for s in finished:
        if s.name != "knowledge_chat.service":
            assert s.parent is not None
            assert s.parent.span_id == boundary.context.span_id  # type: ignore[union-attr]
            assert s.end_time <= boundary.end_time  # type: ignore[operator]


def test_corrective_retry_counts_and_final_validation(spans: InMemorySpanExporter) -> None:
    bad = candidate()
    bad["claims"][0]["pmids"] = ["999"]
    service = KnowledgeChatService(Retriever(EVIDENCE), Answerer([bad, candidate()]), None, SECRET)
    service.chat(KnowledgeChatRequest(message=SECRET), "safe", SECRET)
    finished = spans.get_finished_spans()
    attrs = dict(finished[-1].attributes or {})
    assert attrs["atlas.ask_atlas.generation_attempts"] == 2
    assert attrs["atlas.ask_atlas.validation_outcome"] == "passed"
    assert attrs["atlas.ask_atlas.retrieval_passage_count"] == 1
    assert any(s.name == "knowledge_chat.answer_generation_attempt_2" for s in finished)


def test_closed_attributes_reject_arbitrary_diagnostics(spans: InMemorySpanExporter) -> None:
    with trace.get_tracer(__name__).start_as_current_span("test", record_exception=False):
        _completion_attributes({
            "outcome": SECRET, "validation_outcome": SECRET, "evidence_state": SECRET,
            "model_id": SECRET, "generation_attempts": 999,
            "retrieval_passage_count": 99999, "retrieval_paper_count": -100,
            "request": SECRET, "stage_latencies_ms": {SECRET: SECRET},
        })
    attrs = dict(spans.get_finished_spans()[0].attributes or {})
    assert SECRET not in json.dumps(attrs)
    assert attrs["atlas.ask_atlas.outcome"] == "unhandled_error"
    assert attrs["atlas.ask_atlas.generation_attempts"] == 2
    assert attrs["atlas.ask_atlas.retrieval_passage_count"] == 100
    assert attrs["atlas.ask_atlas.retrieval_paper_count"] == 0
