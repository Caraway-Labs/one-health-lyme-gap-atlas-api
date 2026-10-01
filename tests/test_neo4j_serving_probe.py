"""Startup census is fixed, read-only, bounded, and excludes sensitive values."""

import logging
from typing import Any

from neo4j import READ_ACCESS

from lyme_gap_atlas_api.knowledge_chat import _neo4j_failure_category
from lyme_gap_atlas_api.neo4j_diagnostics import (
    COUNTS_QUERY,
    IDENTITY_QUERY,
    INDEX_QUERY,
    log_serving_graph_probe,
)

SECRET = "private-host-user-password-prompt-response"


class Record:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def data(self) -> dict[str, Any]:
        return self.row


class Driver:
    def __init__(self, failure: Exception | None = None) -> None:
        self.calls: list[Any] = []
        self.failure = failure

    def session(self, **kwargs: Any) -> Any:
        assert kwargs == {"database": "neo4j", "default_access_mode": READ_ACCESS}
        return self

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def run(self, query: Any) -> Any:
        self.calls.append(query)
        assert query.timeout == 1.0
        if self.failure:
            raise self.failure
        if str(query) == IDENTITY_QUERY:
            return [Record({"id": SECRET, "name": "neo4j", "ignored": SECRET})]
        if str(query) == COUNTS_QUERY:
            return [
                Record(
                    {
                        "paper_nodes": 100,
                        "unique_pmids": 100,
                        "passage_nodes": 222,
                        "embedding_1024_nodes": 222,
                        "missing_paper_joins": 0,
                        "raw_vector_hits": 20,
                        "joined_rows": 20,
                        "unmatched_hits": 0,
                        "joined_unique_pmids": 8,
                        "ignored": SECRET,
                    }
                )
            ]
        assert str(query) == INDEX_QUERY
        return [
            Record(
                {
                    "name": "evidence_passage_summary",
                    "type": "VECTOR",
                    "state": "ONLINE",
                    "populationPercent": 100.0,
                    "labelsOrTypes": ["EvidencePassage"],
                    "properties": ["embedding"],
                    "options": {"indexConfig": {"vector.dimensions": 1024, "ignored": SECRET}},
                    "ignored": SECRET,
                }
            )
        ]


def test_fixed_autocommit_probe_logs_counts_and_hash_only(caplog: Any) -> None:
    driver = Driver()
    with caplog.at_level(logging.INFO):
        log_serving_graph_probe(driver, _neo4j_failure_category)  # type: ignore[arg-type]
    assert len(driver.calls) == 3
    contexts = [r.context for r in caplog.records]
    assert contexts[1]["paper_nodes"] == 100
    assert contexts[1]["raw_vector_hits"] == 20
    assert contexts[1]["joined_rows"] == 20
    assert len(contexts[0]["database_id_sha256"]) == 64
    assert contexts[2]["indexes"][0]["expected_vector_dimension"]
    assert SECRET not in str(contexts)
    assert SECRET not in caplog.text


def test_probe_failure_is_private_and_never_raises(caplog: Any) -> None:
    driver = Driver(TimeoutError(SECRET))
    log_serving_graph_probe(driver, _neo4j_failure_category)  # type: ignore[arg-type]
    assert len(driver.calls) == 3
    assert all(r.context["error_category"] == "timeout" and not r.exc_info for r in caplog.records)
    assert SECRET not in caplog.text
    assert SECRET not in str([r.context for r in caplog.records])


def test_probe_query_contains_no_generation_or_paper_filter() -> None:
    assert "probe.embedding" in COUNTS_QUERY
    assert "queryNodes('evidence_passage_summary',20" in COUNTS_QUERY
    assert "OPTIONAL MATCH (joined:Paper" in COUNTS_QUERY
    assert "pmid IN" not in COUNTS_QUERY
    assert "CREATE" not in COUNTS_QUERY


def test_retriever_constructor_runs_probe_once(monkeypatch: Any) -> None:
    import lyme_gap_atlas_api.knowledge_chat as chat

    calls: list[Any] = []
    driver = object()
    monkeypatch.setattr(chat.GraphDatabase, "driver", lambda *args, **kwargs: driver)
    monkeypatch.setattr(chat, "log_serving_graph_probe", lambda *args: calls.append(args))

    class Thread:
        def __init__(self, target: Any, args: Any, name: str, daemon: bool) -> None:
            assert name == "neo4j-serving-probe" and daemon is True
            self.target, self.args = target, args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(chat, "Thread", Thread)
    chat.Neo4jRetriever("bolt://unused", "unused", SECRET, None)  # type: ignore[arg-type]
    assert len(calls) == 1 and calls[0][0] is driver
