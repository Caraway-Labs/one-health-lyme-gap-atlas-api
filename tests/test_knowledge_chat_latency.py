"""Bounded, privacy-safe literature-chat latency and retry behavior."""

from __future__ import annotations

import json
import logging
from typing import Any

from lyme_gap_atlas_api.knowledge_chat import (
    Evidence,
    KnowledgeChatService,
    Neo4jRetriever,
    OpenAIAnswerer,
)
from lyme_gap_atlas_api.models import KnowledgeChatRequest

EXCERPT = "In Germany, Ixodes abundance was associated with Borrelia prevalence."
EVIDENCE = [
    Evidence(
        "passage-1",
        EXCERPT,
        "Governed full-text passage",
        "12345678",
        "Paper 12345678",
        "https://pubmed.ncbi.nlm.nih.gov/12345678/",
    )
]


def valid_payload() -> dict[str, Any]:
    return {
        "answer": EXCERPT,
        "evidence_state": "single_study",
        "claims": [
            {
                "claim_id": "claim-1",
                "text": EXCERPT,
                "passage_ids": ["passage-1"],
                "pmids": ["12345678"],
                "support_quotes": {"passage-1": EXCERPT},
            }
        ],
    }


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class Retriever:
    def ready(self) -> bool:
        return True

    def search(self, message: str, request_id: str) -> list[Evidence]:
        return EVIDENCE


class Answerer:
    def __init__(
        self, results: list[dict[str, Any] | Exception], clock: Clock, elapsed: float = 0
    ) -> None:
        self.results = results
        self.clock = clock
        self.elapsed = elapsed
        self.calls: list[tuple[float, bool]] = []

    def answer(
        self,
        message: str,
        evidence: list[Evidence],
        safety_id: str,
        *,
        timeout_seconds: float,
        correction: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((timeout_seconds, correction))
        self.clock.now += self.elapsed
        result = self.results[len(self.calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result


class Store:
    def __init__(self, fail_persist: bool = False) -> None:
        self.fail_persist = fail_persist
        self.persist_calls = 0

    def authorize(self, conversation_id: str, token_hash: str) -> bool:
        return True

    def reserve(self, request_id: str) -> bool:
        return True

    def persist(self, **kwargs: Any) -> None:
        self.persist_calls += 1
        if self.fail_persist:
            raise RuntimeError("persistence unavailable")


def run(
    results: list[dict[str, Any] | Exception],
    *,
    deadline: float = 24,
    elapsed: float = 0,
    store: Store | None = None,
) -> tuple[Any, Answerer]:
    clock = Clock()
    answerer = Answerer(results, clock, elapsed)
    service = KnowledgeChatService(
        Retriever(), answerer, store, "test-secret", deadline_seconds=deadline, clock=clock
    )
    response = service.chat(
        KnowledgeChatRequest(message="What did the study find?"), "req-1", "network"
    )
    return response, answerer


def test_transport_timeout_uses_one_attempt_and_fails_closed(caplog: Any) -> None:
    caplog.set_level(logging.INFO)
    response, answerer = run([TimeoutError("provider timed out"), valid_payload()])
    assert response.status == "evidence_unavailable"
    assert response.citations == []
    assert len(answerer.calls) == 1
    assert any(
        record.msg == "knowledge_chat_total" and record.context["outcome"] == "generation_timeout"
        for record in caplog.records
    )
    assert "What did the study find?" not in caplog.text


def test_transport_error_does_not_trigger_grounding_retry(caplog: Any) -> None:
    caplog.set_level(logging.INFO)
    response, answerer = run([OSError("transport failed"), valid_payload()])
    assert response.status == "evidence_unavailable"
    assert len(answerer.calls) == 1
    assert any(
        record.msg == "knowledge_chat_total"
        and record.context["outcome"] == "generation_transport_error"
        for record in caplog.records
    )


def test_grounding_failure_gets_one_corrective_retry() -> None:
    invalid = valid_payload()
    invalid["claims"][0]["support_quotes"] = {"passage-1": "invented quote"}
    response, answerer = run([invalid, valid_payload()])
    assert response.status == "answered"
    assert response.citations[0].pmid == "12345678"
    assert [correction for _, correction in answerer.calls] == [False, True]


def test_repeated_grounding_failure_has_no_third_call() -> None:
    invalid = valid_payload()
    invalid["claims"][0]["pmids"] = ["99999999"]
    response, answerer = run([invalid, invalid, valid_payload()])
    assert response.status == "evidence_unavailable"
    assert response.citations == []
    assert len(answerer.calls) == 2


def test_deadline_exhaustion_skips_second_generation() -> None:
    invalid = valid_payload()
    invalid["claims"][0]["passage_ids"] = ["invented"]
    response, answerer = run([invalid, valid_payload()], deadline=12, elapsed=9)
    assert response.status == "evidence_unavailable"
    assert len(answerer.calls) == 1


def test_success_retains_citation_and_persistence() -> None:
    store = Store()
    response, answerer = run([valid_payload()], store=store)
    assert response.status == "answered"
    assert response.citations[0].pubmed_url == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert response.citations[0].passage_ids == ["passage-1"]
    assert store.persist_calls == 1
    assert len(answerer.calls) == 1


def test_request_scoped_timing_is_structured_and_private(caplog: Any) -> None:
    caplog.set_level(logging.INFO)
    response, _ = run([valid_payload()], store=Store())
    assert response.status == "answered"
    contexts = [record.context for record in caplog.records if record.msg == "knowledge_chat_stage"]
    assert {item["stage"] for item in contexts} >= {
        "safety_classification",
        "neo4j_readiness",
        "budget_reservation",
        "answer_generation_attempt_1",
        "grounding_validation_attempt_1",
        "provenance_enrichment",
        "conversation_persistence",
    }
    assert all(item["request_id"] == "req-1" and item["duration_ms"] >= 0 for item in contexts)
    assert "What did the study find?" not in caplog.text
    assert "12345678" not in caplog.text


def test_retrieval_logs_embedding_and_fixed_neo4j_stages(caplog: Any) -> None:
    class Embeddings:
        def create(self, **kwargs: Any) -> Any:
            return type(
                "EmbeddingResult", (), {"data": [type("Vector", (), {"embedding": [0.1]})()]}
            )()

    class OpenAIClient:
        embeddings = Embeddings()

        def with_options(self, *, max_retries: int, timeout: float) -> OpenAIClient:
            assert max_retries == 0
            assert timeout == 5
            return self

    class Driver:
        def execute_query(self, query: Any, parameters: Any, **kwargs: Any) -> Any:
            return (
                [
                    {
                        "passage_id": "passage-1",
                        "excerpt": EXCERPT,
                        "summary": "Governed full-text passage",
                        "pmid": "12345678",
                        "title": "Paper 12345678",
                        "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                    }
                ],
                None,
                None,
            )

    retriever = Neo4jRetriever.__new__(Neo4jRetriever)
    retriever._openai = OpenAIClient()  # type: ignore[assignment]
    retriever._driver = Driver()  # type: ignore[assignment]
    caplog.set_level(logging.INFO)
    assert retriever.search("question", "req-1")[0].pmid == "12345678"
    contexts = [record.context for record in caplog.records if record.msg == "knowledge_chat_stage"]
    assert [item["stage"] for item in contexts] == ["embedding", "neo4j_retrieval"]
    assert all(item["request_id"] == "req-1" for item in contexts)


def test_persistence_failure_fails_closed() -> None:
    store = Store(fail_persist=True)
    response, answerer = run([valid_payload()], store=store)
    assert response.status == "evidence_unavailable"
    assert store.persist_calls == 1
    assert len(answerer.calls) == 1


def test_production_shaped_twenty_passages_keep_grounded_answer() -> None:
    class FullRetriever(Retriever):
        def search(self, message: str, request_id: str) -> list[Evidence]:
            return [
                Evidence(
                    f"passage-{index}",
                    EXCERPT,
                    "Governed full-text passage",
                    str(12345677 + index),
                    f"Paper {index}",
                    f"https://pubmed.ncbi.nlm.nih.gov/{12345677 + index}/",
                )
                for index in range(1, 21)
            ]

    class SizedAnswerer(Answerer):
        evidence_count = 0

        def answer(
            self, message: str, evidence: list[Evidence], safety_id: str, **kwargs: Any
        ) -> dict[str, Any]:
            self.evidence_count = len(evidence)
            return super().answer(message, evidence, safety_id, **kwargs)

    clock = Clock()
    answerer = SizedAnswerer([valid_payload()], clock)
    store = Store()
    service = KnowledgeChatService(
        FullRetriever(), answerer, store, "test-secret", deadline_seconds=24, clock=clock
    )
    result = service.chat(
        KnowledgeChatRequest(message="What do studies say about ticks and Borrelia?"),
        "req-20",
        "network",
    )
    assert answerer.evidence_count == 20
    assert result.status == "answered"
    assert result.citations[0].pmid == "12345678"
    assert store.persist_calls == 1


def test_answer_specific_openai_client_has_no_sdk_retries() -> None:
    class Responses:
        def __init__(self) -> None:
            self.timeout = 0.0
            self.instructions = ""

        def create(self, **kwargs: Any) -> Any:
            self.timeout = kwargs["timeout"]
            self.instructions = kwargs["instructions"]
            return type("Response", (), {"output_text": json.dumps(valid_payload())})()

    class Client:
        def __init__(self) -> None:
            self.max_retries: int | None = None
            self.responses = Responses()

        def with_options(self, *, max_retries: int) -> Client:
            self.max_retries = max_retries
            return self

    client = Client()
    answerer = OpenAIAnswerer(client)  # type: ignore[arg-type]
    result = answerer.answer("question", EVIDENCE, "safety-id", timeout_seconds=7, correction=True)
    assert result["evidence_state"] == "single_study"
    assert client.max_retries == 0
    assert client.responses.timeout == 7
    assert "previous candidate failed deterministic grounding" in client.responses.instructions
