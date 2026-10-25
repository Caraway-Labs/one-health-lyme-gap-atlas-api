"""Provenance is looked up beside generation, cached, and never fails an answer (#302)."""

from __future__ import annotations

import logging
import threading
from typing import Any

import pytest
from test_knowledge_chat_latency import Answerer, Clock, Retriever, valid_payload

from lyme_gap_atlas_api import knowledge_chat as chat
from lyme_gap_atlas_api.knowledge_chat import KnowledgeChatService, _ProvenanceCache
from lyme_gap_atlas_api.models import KnowledgeChatRequest

WAIT_SECONDS = 5.0
ROW = {
    "pmcid": "PMC999",
    "corpus_unit_ids": ["unit-a"],
    "section_labels": ["Results"],
    "corpus_rules_version": "retrieval-corpus-v1",
    "artifact_id": "artifact-1",
    "contribution_sha256": "c" * 64,
    "jats_sha256": "j" * 64,
}


class Provenance:
    def __init__(self, *, fail: bool = False, block: threading.Event | None = None) -> None:
        self.fail = fail
        self.block = block
        self.calls: list[list[str]] = []
        self.started = threading.Event()
        self.threads: list[int] = []

    def lookup(self, pmids: list[str]) -> dict[str, dict[str, Any]]:
        self.calls.append(list(pmids))
        self.threads.append(threading.get_ident())
        self.started.set()
        if self.block is not None:
            self.block.wait(WAIT_SECONDS)
        if self.fail:
            raise RuntimeError("snowflake unavailable with private detail")
        return {pmid: ROW for pmid in pmids if pmid == "12345678"}


class ProbingAnswerer(Answerer):
    """Records whether the provenance lookup was already running during generation."""

    def __init__(self, results: list[Any], clock: Clock, store: Provenance, elapsed: float = 0):
        super().__init__(results, clock, elapsed)
        self.store = store
        self.lookup_started_during_generation: bool | None = None

    def answer(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.lookup_started_during_generation = self.store.started.wait(WAIT_SECONDS)
        return super().answer(*args, **kwargs)


def service_for(
    store: Provenance, *, deadline: float = 24, elapsed: float = 0
) -> tuple[KnowledgeChatService, ProbingAnswerer]:
    clock = Clock()
    answerer = ProbingAnswerer([valid_payload()] * 3, clock, store, elapsed)
    service = KnowledgeChatService(
        Retriever(),
        answerer,
        None,
        "test-secret",
        store,
        deadline_seconds=deadline,
        clock=clock,
        prefetch_provenance=True,
    )
    return service, answerer


def ask(service: KnowledgeChatService, rid: str = "req-p") -> Any:
    return service.chat(KnowledgeChatRequest(message="What did the study find?"), rid, "network")


def test_lookup_starts_before_generation_and_enriches_citations() -> None:
    store = Provenance()
    service, answerer = service_for(store)
    response = ask(service)
    assert response.status == "answered"
    assert answerer.lookup_started_during_generation is True
    citation = response.citations[0]
    assert citation.pmcid == "PMC999"
    assert citation.corpus_unit_ids == ["unit-a"]
    assert citation.section_labels == ["Results"]
    assert citation.artifact_id == "artifact-1"
    assert citation.jats_sha256 == "j" * 64
    assert store.calls == [["12345678"]]
    assert store.threads[0] != threading.get_ident()


def test_lookup_failure_returns_unenriched_answer_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    service, _ = service_for(Provenance(fail=True))
    response = ask(service)
    assert response.status == "answered"
    assert response.citations[0].pmcid is None
    names = [r.getMessage() for r in caplog.records]
    assert "retrieval_corpus.provenance_lookup_failed" in names
    assert "private detail" not in caplog.text


def test_slow_lookup_times_out_without_failing_the_answer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(chat, "_PROVENANCE_MIN_WAIT_SECONDS", 0.2)
    release = threading.Event()
    store = Provenance(block=release)
    # 9 s deadline minus 7.6 s of generation leaves less than the minimum wait.
    service, _ = service_for(store, deadline=9, elapsed=7.6)
    response = ask(service)
    release.set()
    assert response.status == "answered"
    assert response.citations[0].pmcid is None
    prefetch = [r for r in caplog.records if r.getMessage() == "knowledge_chat_provenance_prefetch"]
    assert prefetch[0].context["outcome"] == "failure"  # type: ignore[attr-defined]


def test_repeat_pmids_hit_the_cache_and_skip_snowflake(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    store = Provenance()
    service, _ = service_for(store)
    first = ask(service, "req-1")
    second = ask(service, "req-2")
    assert first.citations[0].pmcid == second.citations[0].pmcid == "PMC999"
    assert store.calls == [["12345678"]]
    prefetch = [r for r in caplog.records if r.getMessage() == "knowledge_chat_provenance_prefetch"]
    assert [r.context["cache_hit_count"] for r in prefetch] == [0, 1]  # type: ignore[attr-defined]
    assert all("wait_ms" in r.context for r in prefetch)  # type: ignore[attr-defined]


def test_default_service_keeps_the_synchronous_lookup() -> None:
    store = Provenance()
    clock = Clock()
    service = KnowledgeChatService(
        Retriever(), Answerer([valid_payload()], clock), None, "test-secret", store, clock=clock
    )
    response = ask(service)
    assert response.citations[0].pmcid == "PMC999"
    assert store.threads == [threading.get_ident()]


def test_cache_expires_and_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    cache = _ProvenanceCache(clock=lambda: now[0])
    cache.put("a", ROW)
    cache.put("missing", None)
    assert cache.get("a") == (True, ROW)
    assert cache.get("missing") == (True, None)
    now[0] = chat._PROVENANCE_ABSENT_TTL_SECONDS + 1
    assert cache.get("missing") == (False, None)
    assert cache.get("a") == (True, ROW)
    now[0] = chat._PROVENANCE_CACHE_TTL_SECONDS + 1
    assert cache.get("a") == (False, None)

    monkeypatch.setattr(chat, "_PROVENANCE_CACHE_MAX", 2)
    bounded = _ProvenanceCache(clock=lambda: now[0])
    for index, pmid in enumerate(["p1", "p2", "p3"]):
        now[0] += 1
        bounded.put(pmid, {**ROW, "artifact_id": str(index)})
    assert bounded.get("p1") == (False, None)
    assert bounded.get("p2")[0] and bounded.get("p3")[0]
    bounded.put("p3", ROW)
    assert bounded.get("p2")[0]


def test_prefetch_edge_paths_never_raise() -> None:
    clock = Clock()
    bare = KnowledgeChatService(
        Retriever(), Answerer([valid_payload()], clock), None, "test-secret", clock=clock
    )
    assert bare._provenance_rows(["12345678"]).rows == {}
    assert bare._start_provenance_prefetch([], "req-e") is None

    service, _ = service_for(Provenance())
    assert service._start_provenance_prefetch([], "req-e") is None
    service._provenance_executor.shutdown(wait=True)  # type: ignore[union-attr]
    evidence = chat.Evidence("p1", "excerpt", "summary", "12345678", "Title", "https://x.test/1")
    assert service._start_provenance_prefetch([evidence], "req-e") is None
    assert ask(service).citations[0].pmcid == "PMC999"  # falls back to the synchronous lookup
