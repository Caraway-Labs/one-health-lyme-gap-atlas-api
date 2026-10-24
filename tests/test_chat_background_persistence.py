"""Conversation persistence runs after the response and never changes it (#301)."""

from __future__ import annotations

import logging
import threading
from typing import Any

import pytest
from test_knowledge_chat_latency import Answerer, Clock, Retriever, valid_payload

from lyme_gap_atlas_api import knowledge_chat as chat
from lyme_gap_atlas_api.knowledge_chat import Evidence, KnowledgeChatService
from lyme_gap_atlas_api.models import KnowledgeChatRequest

WAIT_SECONDS = 5.0
QUESTION = "What did the study find?"


class GatedStore:
    """Persist blocks until released, so ordering against the response is observable."""

    def __init__(self, *, fail: bool = False, gate: bool = True) -> None:
        self.fail = fail
        self.release = threading.Event()
        if not gate:
            self.release.set()
        self.started = threading.Event()
        self.finished = threading.Event()
        self.calls: list[dict[str, Any]] = []
        self.threads: list[int] = []

    def authorize(self, conversation_id: str, token_hash: str) -> bool:
        return True

    def reserve(self, request_id: str) -> bool:
        raise AssertionError("Spending approval must not be called")

    def persist(self, **kwargs: Any) -> None:
        self.started.set()
        self.threads.append(threading.get_ident())
        assert self.release.wait(WAIT_SECONDS)
        self.calls.append(kwargs)
        self.finished.set()
        if self.fail:
            raise RuntimeError("private persistence error with secret-prompt-text")


class EmptyRetriever(Retriever):
    def search(self, message: str, request_id: str) -> list[Evidence]:
        return []


def service_for(
    store: GatedStore, retriever: Retriever | None = None
) -> tuple[KnowledgeChatService, Answerer]:
    clock = Clock()
    answerer = Answerer([valid_payload(), valid_payload()], clock)
    service = KnowledgeChatService(
        retriever or Retriever(),
        answerer,
        store,
        "test-secret",
        clock=clock,
        persist_in_background=True,
    )
    return service, answerer


def ask(service: KnowledgeChatService, message: str = QUESTION, rid: str = "req-bg") -> Any:
    return service.chat(KnowledgeChatRequest(message=message), rid, "network")


def test_answer_returns_before_persistence_completes() -> None:
    store = GatedStore()
    service, _ = service_for(store)
    response = ask(service)
    assert response.status == "answered"
    assert response.citations
    assert not store.finished.is_set()
    store.release.set()
    assert store.finished.wait(WAIT_SECONDS)
    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["request_id"] == "req-bg"
    assert call["response"].status == "answered"
    assert call["request"].message == QUESTION
    assert store.threads[0] != threading.get_ident()


def test_no_evidence_and_safety_refusal_also_persist_in_background() -> None:
    empty = GatedStore()
    service, _ = service_for(empty, EmptyRetriever())
    assert ask(service).status == "no_evidence"
    assert not empty.finished.is_set()
    empty.release.set()
    assert empty.finished.wait(WAIT_SECONDS)

    refused = GatedStore()
    service, _ = service_for(refused)
    assert ask(service, "Should I take a higher dose of doxycycline?").status == "safety_refusal"
    assert not refused.finished.is_set()
    refused.release.set()
    assert refused.finished.wait(WAIT_SECONDS)
    assert refused.calls[0]["response"].status == "safety_refusal"


def test_persistence_failure_keeps_answer_and_logs_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    store = GatedStore(fail=True, gate=False)
    service, _ = service_for(store)
    response = ask(service)
    assert response.status == "answered"
    assert store.finished.wait(WAIT_SECONDS)
    service._persist_executor.shutdown(wait=True)  # type: ignore[union-attr]
    failures = [r for r in caplog.records if r.getMessage() == "knowledge_chat_persistence_failed"]
    assert len(failures) == 1
    context = failures[0].context  # type: ignore[attr-defined]
    assert context["request_id"] == "req-bg"
    assert context["background"] is True
    assert "secret-prompt-text" not in caplog.text
    assert QUESTION not in caplog.text


def test_full_queue_drops_without_blocking_the_request(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(chat, "_PERSIST_QUEUE_LIMIT", 1)
    store = GatedStore()
    service, _ = service_for(store)
    first = ask(service, rid="req-1")
    assert store.started.wait(WAIT_SECONDS)
    second = ask(service, rid="req-2")
    assert first.status == second.status == "answered"
    failures = [r for r in caplog.records if r.getMessage() == "knowledge_chat_persistence_failed"]
    assert [r.context["error_type"] for r in failures] == ["queue_full"]  # type: ignore[attr-defined]
    store.release.set()
    assert store.finished.wait(WAIT_SECONDS)
    service._persist_executor.shutdown(wait=True)  # type: ignore[union-attr]
    assert len(store.calls) == 1


def test_default_service_still_persists_synchronously() -> None:
    store = GatedStore(gate=False)
    clock = Clock()
    service = KnowledgeChatService(
        Retriever(), Answerer([valid_payload()], clock), store, "test-secret", clock=clock
    )
    response = ask(service)
    assert response.status == "answered"
    assert len(store.calls) == 1
    assert store.threads == [threading.get_ident()]
