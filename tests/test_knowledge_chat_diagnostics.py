"""Operational diagnostics retain structure, never generated or retrieved content."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from opentelemetry import trace

from lyme_gap_atlas_api.knowledge_chat import (
    EmbeddingFailure,
    Evidence,
    KnowledgeChatService,
    Neo4jQueryFailure,
    OpenAIAnswerer,
    _shape_diagnostics,
)
from lyme_gap_atlas_api.models import KnowledgeChatRequest

SECRET = "private-question-passage-quote-prompt-response-key"
EVIDENCE = [Evidence("p1", SECRET, SECRET, "123", SECRET, "https://example.org")]


class Retriever:
    def __init__(self, evidence: list[Evidence] | Exception) -> None:
        self.evidence = evidence

    def ready(self) -> bool:
        return True

    def search(self, message: str, request_id: str) -> list[Evidence]:
        if isinstance(self.evidence, Exception):
            raise self.evidence
        return self.evidence


class Answerer:
    model_id = "test-model"

    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls = 0

    def answer(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]


def candidate() -> dict[str, Any]:
    return {
        "answer": SECRET,
        "evidence_state": "single_study",
        "claims": [
            {
                "claim_id": "c1",
                "text": SECRET,
                "passage_ids": ["p1"],
                "pmids": ["123"],
                "support_quotes": {"p1": SECRET},
            }
        ],
    }


@pytest.mark.parametrize(
    ("evidence", "results", "outcome"),
    [
        ([], [], "no_evidence"),
        (RuntimeError(SECRET), [], "retrieval_dependency_unavailable"),
        (EmbeddingFailure(), [], "embedding_failure"),
        (Neo4jQueryFailure(), [], "neo4j_query_failure"),
        (EVIDENCE, [json.JSONDecodeError(SECRET, SECRET, 0)], "malformed_generated_json"),
        (EVIDENCE, [candidate()], "answered"),
    ],
)
def test_completion_categories_are_private(
    evidence: list[Evidence] | Exception, results: list[object], outcome: str, caplog: Any
) -> None:
    caplog.set_level(logging.INFO)
    service = KnowledgeChatService(Retriever(evidence), Answerer(results), None, SECRET)
    service.chat(KnowledgeChatRequest(message=SECRET), "safe-id", SECRET)
    completions = [r.context for r in caplog.records if r.msg == "knowledge_chat_total"]
    assert len(completions) == 1
    assert completions[0]["outcome"] == outcome
    assert SECRET not in caplog.text


def test_quote_shape_and_retry_are_bounded_and_private(caplog: Any) -> None:
    caplog.set_level(logging.INFO)
    invalid = candidate()
    invalid["claims"][0]["support_quotes"] = [
        {"passage_id": "p1", "quote": SECRET},
        {"passage_id": "p1", "quote": SECRET},
    ]
    service = KnowledgeChatService(
        Retriever(EVIDENCE), Answerer([invalid, candidate()]), None, SECRET
    )
    result = service.chat(KnowledgeChatRequest(message=SECRET), "safe-id", SECRET)
    assert result.status == "answered"
    rejected = [r.context for r in caplog.records if r.msg == "knowledge_chat_grounding_rejected"]
    assert rejected[0]["reason"] == "each cited passage needs an exact support quote"
    shape = rejected[0]["shape"]["claim_shapes"][0]
    assert shape["quote_representation"] == "record_list"
    assert shape["duplicate_quote_id_count"] == 1
    assert SECRET not in caplog.text


def test_citation_mismatch_and_exhaustion(caplog: Any) -> None:
    caplog.set_level(logging.INFO)
    invalid = candidate()
    invalid["claims"][0]["pmids"] = ["999"]
    service = KnowledgeChatService(Retriever(EVIDENCE), Answerer([invalid, invalid]), None, SECRET)
    result = service.chat(KnowledgeChatRequest(message=SECRET), "safe-id", SECRET)
    assert result.status == "evidence_unavailable"
    completion = [r.context for r in caplog.records if r.msg == "knowledge_chat_total"][0]
    assert completion["outcome"] == "corrective_retry_exhausted"
    assert completion["validation_outcome"] == "invented or missing PMID"
    assert completion["generation_attempts"] == 2
    assert SECRET not in caplog.text


def test_shape_omits_all_candidate_content() -> None:
    shape = _shape_diagnostics(candidate(), EVIDENCE)
    assert SECRET not in json.dumps(shape)
    assert shape["claim_shapes"][0]["pmid_mismatch"] is False


def test_broken_tracer_does_not_change_answer(monkeypatch: Any) -> None:
    class BrokenTracer:
        def start_span(self, name: str) -> None:
            raise RuntimeError(SECRET)

    monkeypatch.setattr(trace, "get_tracer", lambda name: BrokenTracer())
    service = KnowledgeChatService(Retriever(EVIDENCE), Answerer([candidate()]), None, SECRET)
    result = service.chat(KnowledgeChatRequest(message=SECRET), "safe-id", SECRET)
    assert result.status == "answered"


def test_provider_identifiers_are_not_logged(caplog: Any) -> None:
    class Responses:
        def create(self, **kwargs: Any) -> Any:
            return type("Response", (), {
                "_request_id": "req-provider-123",
                "id": "resp-generation-456",
                "output_text": json.dumps(candidate()),
            })()

    class Client:
        responses = Responses()

        def with_options(self, *, max_retries: int) -> Client:
            return self

    caplog.set_level(logging.INFO)
    answerer = OpenAIAnswerer(Client())  # type: ignore[arg-type]
    answerer.answer(SECRET, EVIDENCE, "safety-id", timeout_seconds=5)
    event = [r.context for r in caplog.records if r.msg == "knowledge_chat_provider_response"][0]
    assert "provider_request_id" not in event
    assert "provider_response_id" not in event
    assert SECRET not in caplog.text
