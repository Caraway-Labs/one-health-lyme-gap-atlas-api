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
from test_knowledge_chat_latency import Answerer as TimedAnswerer
from test_knowledge_chat_latency import Clock, valid_payload
from test_knowledge_chat_latency import Retriever as TimedRetriever

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


@pytest.mark.parametrize("deadline,elapsed,expected_calls", [(7, 0, 0), (12, 9, 1)])
def test_deadline_guard_counts_only_started_generation(
    spans: InMemorySpanExporter, deadline: float, elapsed: float, expected_calls: int,
) -> None:
    clock = Clock()
    rejected = valid_payload()
    rejected["claims"][0]["pmids"] = ["999"]
    answerer = TimedAnswerer([rejected, valid_payload()], clock, elapsed=elapsed)
    service = KnowledgeChatService(
        TimedRetriever(), answerer, None, SECRET, deadline_seconds=deadline, clock=clock,
    )
    completion: dict[str, Any] = {}
    result = service.chat(
        KnowledgeChatRequest(message="What did the study find?"), "safe", SECRET, completion,
    )
    assert result.status == "evidence_unavailable"
    assert completion["outcome"] == "deadline_exhausted"
    assert completion["generation_attempts"] == len(answerer.calls) == expected_calls
    finished = spans.get_finished_spans()
    attrs = dict(finished[-1].attributes or {})
    assert attrs["atlas.ask_atlas.generation_attempts"] == expected_calls
    assert attrs["atlas.ask_atlas.outcome"] == "deadline_exhausted"
    generation_spans = [
        s for s in finished if s.name.startswith("knowledge_chat.answer_generation_")
    ]
    assert len(generation_spans) == expected_calls
    assert all(s.name != "knowledge_chat.answer_generation_attempt_2" for s in finished)
    assert SECRET not in json.dumps([dict(s.attributes or {}) for s in finished])


@pytest.mark.parametrize("use_completion", [True, False])
def test_close_failure_clears_success_and_preserves_exception(
    spans: InMemorySpanExporter, monkeypatch: Any, caplog: Any, use_completion: bool,
) -> None:
    import logging

    from test_chat_snowflake_reuse import Connection

    from lyme_gap_atlas_api import knowledge_chat as chat
    from lyme_gap_atlas_api.config import ApiSettings

    caplog.set_level(logging.INFO)
    clock = Clock()
    failure = RuntimeError(SECRET)

    class FailingClose(Connection):
        def close(self) -> None:
            self.closed = True
            clock.now += 2
            raise failure

    connection = FailingClose()
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    settings = ApiSettings()
    service = KnowledgeChatService(
        TimedRetriever(), TimedAnswerer([valid_payload()], clock, elapsed=1),
        chat.SnowflakeBudgetStore(settings), SECRET,
        chat.SnowflakeCorpusProvenanceStore(settings), clock=clock,
        snowflake_settings=settings,
    )
    completion: dict[str, Any] = {}
    with pytest.raises(RuntimeError) as raised:
        service.chat(KnowledgeChatRequest(message=SECRET), "safe", SECRET,
                     completion if use_completion else None)
    assert raised.value is failure
    contexts = [r.context for r in caplog.records if r.msg == "knowledge_chat_total"]
    context = completion if use_completion else contexts[0]
    assert context["outcome"] == "unhandled_error"
    # The completion dictionary retains None; the governed log schema omits
    # unset enum fields rather than emitting an unbounded value.
    assert context.get("evidence_state") is None
    assert context["service_duration_ms"] == 3000
    finished = spans.get_finished_spans()
    boundary = next(s for s in finished if s.name == "knowledge_chat.service")
    close = next(s for s in finished if s.name == "knowledge_chat.snowflake.connection_close")
    attrs = dict(boundary.attributes or {})
    assert attrs["atlas.ask_atlas.outcome"] == "unhandled_error"
    assert attrs["atlas.ask_atlas.outcome_class"] == "failure"
    assert attrs["atlas.ask_atlas.evidence_state"] == "unavailable"
    assert boundary.status.status_code == trace.StatusCode.ERROR
    assert boundary.status.description is None
    assert boundary.end_time >= close.end_time
    assert boundary.start_time <= close.start_time
    assert SECRET not in json.dumps([dict(s.attributes or {}) for s in finished])
    assert not any(s.events for s in finished)
    assert SECRET not in caplog.text
