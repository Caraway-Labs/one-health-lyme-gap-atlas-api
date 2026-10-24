"""An empty-claims answer is a corpus gap (no_evidence), not an outage (#304)."""

from __future__ import annotations

from typing import Any

from test_knowledge_chat_latency import Answerer, Clock, Retriever, Store, valid_payload

from lyme_gap_atlas_api.knowledge_chat import NO_EVIDENCE, KnowledgeChatService
from lyme_gap_atlas_api.models import KnowledgeChatRequest

EMPTY = {"evidence_state": "insufficient_to_compare", "answer": "", "claims": []}
INVALID_CLAIM = {
    **valid_payload(),
    "claims": [{**valid_payload()["claims"][0], "text": "Unrelated sentence about oceans."}],
}


def ask(
    results: list[dict[str, Any] | Exception], store: Store | None = None
) -> tuple[Any, Answerer, dict[str, Any]]:
    clock = Clock()
    answerer = Answerer(results, clock)
    service = KnowledgeChatService(Retriever(), answerer, store, "test-secret", clock=clock)
    completion: dict[str, Any] = {}
    response = service.chat(
        KnowledgeChatRequest(message="What is known about Powassan virus?"),
        "req-empty",
        "network",
        completion,
    )
    return response, answerer, completion


def test_empty_claims_return_no_evidence_without_retry() -> None:
    store = Store()
    response, answerer, completion = ask([EMPTY], store)
    assert response.status == "no_evidence"
    assert response.evidence_state == "no_relevant_corpus_evidence"
    assert response.answer == NO_EVIDENCE
    assert "does not mean scientific evidence does not exist" in response.answer
    assert response.claims == []
    assert response.citations == []
    assert len(answerer.calls) == 1
    assert store.persist_calls == 1
    assert completion["outcome"] == "no_evidence"
    assert completion["validation_outcome"] == "no_claims"
    assert completion["generation_attempts"] == 1


def test_empty_claims_after_a_rejected_first_attempt_are_still_no_evidence() -> None:
    response, answerer, completion = ask([INVALID_CLAIM, EMPTY])
    assert response.status == "no_evidence"
    assert len(answerer.calls) == 2
    assert completion["outcome"] == "no_evidence"


def test_malformed_output_still_retries_and_fails_closed() -> None:
    response, answerer, completion = ask([{"unexpected": True}, {"unexpected": True}])
    assert response.status == "evidence_unavailable"
    assert len(answerer.calls) == 2
    assert completion["outcome"] == "corrective_retry_exhausted"


def test_ungrounded_claims_still_retry_and_fail_closed() -> None:
    response, answerer, completion = ask([INVALID_CLAIM, INVALID_CLAIM])
    assert response.status == "evidence_unavailable"
    assert len(answerer.calls) == 2
    assert completion["validation_outcome"] != "no_claims"


def test_no_evidence_persistence_failure_fails_closed() -> None:
    store = Store(fail_persist=True)
    response, answerer, completion = ask([EMPTY], store)
    assert response.status == "evidence_unavailable"
    assert len(answerer.calls) == 1
    assert completion["outcome"] == "persistence_failure"
