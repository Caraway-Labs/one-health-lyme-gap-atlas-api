"""Startup census is fixed, read-only, bounded, and excludes sensitive values."""

import logging
from typing import Any

import pytest
from neo4j import READ_ACCESS
from neo4j.exceptions import Neo4jError

from lyme_gap_atlas_api.knowledge_chat import _neo4j_failure_category
from lyme_gap_atlas_api.neo4j_diagnostics import (
    COUNTS_QUERY,
    IDENTITY_QUERY,
    INDEX_QUERY,
    VECTOR_QUERY,
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
        if str(query) in {COUNTS_QUERY, VECTOR_QUERY}:
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
    assert len(driver.calls) == 4
    contexts = [r.context for r in caplog.records]
    assert contexts[1]["paper_nodes"] == 100
    assert contexts[2]["raw_vector_hits"] == 20
    assert contexts[2]["joined_rows"] == 20
    assert len(contexts[0]["database_id_sha256"]) == 64
    assert contexts[3]["indexes"][0]["expected_vector_dimension"]
    assert SECRET not in str(contexts)
    assert SECRET not in caplog.text


def test_probe_failure_is_private_and_never_raises(caplog: Any) -> None:
    driver = Driver(TimeoutError(SECRET))
    log_serving_graph_probe(driver, _neo4j_failure_category)  # type: ignore[arg-type]
    assert len(driver.calls) == 4
    assert all(r.context["error_category"] == "timeout" and not r.exc_info for r in caplog.records)
    assert SECRET not in caplog.text
    assert SECRET not in str([r.context for r in caplog.records])


def test_probe_query_contains_no_generation_or_paper_filter() -> None:
    assert "probe.embedding" in VECTOR_QUERY
    assert "queryNodes('evidence_passage_summary',20" in VECTOR_QUERY
    assert "OPTIONAL MATCH (joined:Paper" in VECTOR_QUERY
    assert "pmid IN" not in VECTOR_QUERY
    assert "CREATE" not in VECTOR_QUERY
    assert "count(DISTINCT hit) AS raw_vector_hits,count(joined) AS joined_rows" in VECTOR_QUERY


def test_duplicate_paper_join_fanout_is_not_normalized_away(caplog: Any) -> None:
    class FanoutDriver(Driver):
        def run(self, query: Any) -> Any:
            rows = super().run(query)
            if str(query) == VECTOR_QUERY:
                rows[0].row.update(raw_vector_hits=20, joined_rows=21, joined_unique_pmids=20)
            return rows

    with caplog.at_level(logging.INFO):
        log_serving_graph_probe(FanoutDriver(), _neo4j_failure_category)  # type: ignore[arg-type]
    counts = [r.context for r in caplog.records if r.context["probe"] == "vector"][0]
    assert counts["raw_vector_hits"] == 20 and counts["joined_rows"] == 21


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


@pytest.mark.parametrize("fail_during", ["construction", "start"])
def test_diagnostic_thread_failure_never_blocks_constructor(
    monkeypatch: Any, caplog: Any, fail_during: str
) -> None:
    import lyme_gap_atlas_api.knowledge_chat as chat

    driver = object()
    monkeypatch.setattr(chat.GraphDatabase, "driver", lambda *args, **kwargs: driver)

    class FailingThread:
        def __init__(self, **kwargs: Any) -> None:
            if fail_during == "construction":
                raise RuntimeError(SECRET)

        def start(self) -> None:
            raise RuntimeError(SECRET)

    monkeypatch.setattr(chat, "Thread", FailingThread)
    retriever = chat.Neo4jRetriever("bolt://unused", "unused", SECRET, None)  # type: ignore[arg-type]
    assert retriever._driver is driver
    assert caplog.records[-1].context["probe"] == "startup"
    assert caplog.records[-1].context["error_category"] == "unknown"
    assert not caplog.records[-1].exc_info and SECRET not in caplog.text


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("Neo.TransientError.Transaction.TransactionTimedOut", "transaction_timeout"),
        (
            "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
            "transaction_timeout",
        ),
        ("Neo.ClientError.Statement.SyntaxError", "query_syntax"),
        ("Neo.ClientError.Statement.TypeError", "query_type"),
        ("Neo.ClientError.Statement.ArgumentError", "query_argument"),
        ("Neo.ClientError.Procedure.ProcedureCallFailed", "procedure_failure"),
        ("Neo.ClientError.Security.Forbidden", "access_denied"),
        (SECRET, "other_neo4j_error"),
    ],
)
def test_known_server_error_categories_never_copy_code_or_message(
    code: str, category: str, caplog: Any
) -> None:
    error = Neo4jError(SECRET)
    error._neo4j_code = code
    log_serving_graph_probe(Driver(error), _neo4j_failure_category)  # type: ignore[arg-type]
    assert all(r.context["server_error_category"] == category for r in caplog.records)
    assert all(
        r.context["failure_stage"] == "query_execution" and r.context["elapsed_ms"] >= 0
        for r in caplog.records
    )
    assert SECRET not in str([r.context for r in caplog.records]) and SECRET not in caplog.text


def test_vector_failure_keeps_successful_simple_census(caplog: Any) -> None:
    class VectorFailure(Driver):
        def run(self, query: Any) -> Any:
            if str(query) == VECTOR_QUERY:
                raise TimeoutError(SECRET)
            return super().run(query)

    with caplog.at_level(logging.INFO):
        log_serving_graph_probe(VectorFailure(), _neo4j_failure_category)  # type: ignore[arg-type]
    records = {r.context["probe"]: r.context for r in caplog.records}
    assert records["counts"]["paper_nodes"] == 100
    assert records["vector"]["error_category"] == "timeout"
    assert records["indexes"]["outcome"] == "success"


def test_local_shape_failure_is_distinct_and_private(caplog: Any) -> None:
    class InvalidShape(Driver):
        def run(self, query: Any) -> Any:
            rows = super().run(query)
            if str(query) == COUNTS_QUERY:
                rows[0].row["paper_nodes"] = SECRET
            return rows

    with caplog.at_level(logging.INFO):
        log_serving_graph_probe(InvalidShape(), _neo4j_failure_category)  # type: ignore[arg-type]
    failed = [r.context for r in caplog.records if r.context["outcome"] == "failure"]
    assert failed[0]["failure_stage"] == "shape_validation"
    assert failed[0]["server_error_category"] == "not_neo4j_error"
    assert SECRET not in str(failed) and SECRET not in caplog.text
