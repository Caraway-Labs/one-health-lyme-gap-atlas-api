"""Exclusive request lifetime, unchanged procedure ordering, and failure cleanup."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Barrier, get_ident
from typing import Any

import pytest
from test_knowledge_chat_latency import Answerer, Clock, Retriever, valid_payload

from lyme_gap_atlas_api import knowledge_chat as chat
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.models import KnowledgeChatRequest


class Cursor:
    sfqid = "query-test"

    def __init__(self, connection: "Connection") -> None:
        self.connection = connection
        self.sql = ""

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def execute(self, sql: str, params: Any) -> None:
        assert self.connection.owner == get_ident()
        self.sql = sql
        self.connection.calls.append((sql, params))
        if self.connection.failure and self.connection.failure in sql:
            raise RuntimeError("private dependency error")

    def fetchone(self) -> tuple[Any]:
        if "RESERVE" in self.sql:
            return ({"allowed": self.connection.allowed},)
        if "VERIFY" in self.sql:
            return (self.connection.authorized,)
        return ([],)


class Connection:
    def __init__(self, *, failure: str = "", allowed: bool = True) -> None:
        self.owner = get_ident()
        self.failure = failure
        self.allowed = allowed
        self.authorized = True
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self._session_parameters: dict[str, Any] = {}
        self.calls: list[tuple[str, Any]] = []

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *args: Any) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def cursor(self) -> Cursor:
        return Cursor(self)


def service(settings: ApiSettings, results: list[Any] | None = None) -> chat.KnowledgeChatService:
    return chat.KnowledgeChatService(
        Retriever(),
        Answerer(results or [valid_payload()], Clock()),
        chat.SnowflakeBudgetStore(settings),
        "secret",
        chat.SnowflakeCorpusProvenanceStore(settings),
        snowflake_settings=settings,
    )


def test_answer_reuses_one_connection_and_preserves_sequential_turns(
    monkeypatch: Any,
    caplog: Any,
) -> None:
    import logging

    caplog.set_level(logging.INFO)
    connections: list[Connection] = []

    def connect(settings: Any) -> Connection:
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(chat, "connect", connect)
    result = service(ApiSettings()).chat(KnowledgeChatRequest(message="private question"), "r", "n")
    assert result.status == "answered"
    assert len(connections) == 1
    connection = connections[0]
    assert connection.closed
    assert connection.commits == 3
    assert [sql.split("SP_")[1].split("(")[0] for sql, _ in connection.calls] == [
        "RESERVE_KG_LLM_BUDGET",
        "LOOKUP_RETRIEVAL_CORPUS_PROVENANCE",
        "PERSIST_KG_CONVERSATION_TURN",
        "PERSIST_KG_CONVERSATION_TURN",
    ]
    assert [params[5] for _, params in connection.calls[-2:]] == ["user", "assistant"]
    assert "private question" not in caplog.text
    assert any(r.context.get("operation") == "connection_setup" for r in caplog.records)
    assert all(
        "snowflake" not in k
        for r in caplog.records
        if r.msg == "knowledge_chat_total"
        for k in r.context["stage_latencies_ms"]
    )


@pytest.mark.parametrize("failure", ["RESERVE", "PERSIST", ""])
def test_failures_close_session_without_replaying_writes(monkeypatch: Any, failure: str) -> None:
    connection = Connection(failure=failure)
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    results = [TimeoutError()] if not failure else None
    result = service(ApiSettings(), results).chat(KnowledgeChatRequest(message="study?"), "r", "n")
    assert result.status == "evidence_unavailable"
    assert connection.closed
    assert sum("RESERVE" in sql for sql, _ in connection.calls) == 1
    assert sum("PERSIST" in sql for sql, _ in connection.calls) <= 1


def test_budget_denial_never_generates_and_closes(monkeypatch: Any) -> None:
    connection = Connection(allowed=False)
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    result = service(ApiSettings()).chat(KnowledgeChatRequest(message="study?"), "r", "n")
    assert result.status == "capacity_limited"
    assert len(connection.calls) == 1
    assert connection.closed


def test_concurrent_requests_never_share_sessions(monkeypatch: Any) -> None:
    barrier = Barrier(2)
    connections: list[Connection] = []

    def connect(settings: Any) -> Connection:
        connection = Connection()
        connections.append(connection)
        barrier.wait(timeout=5)
        return connection

    monkeypatch.setattr(chat, "connect", connect)
    shared = service(ApiSettings())
    # Avoid sharing the stateful answerer fixture across workers.
    shared._answerer = type(
        "FixedAnswerer",
        (),
        {
            "answer": lambda self, *args, **kwargs: valid_payload(),
        },
    )()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda i: shared.chat(KnowledgeChatRequest(message="study?"), f"r{i}", "n"),
                [1, 2],
            )
        )
    assert all(result.status == "answered" for result in results)
    assert len(connections) == 2
    assert all(connection.closed and len(connection.calls) == 4 for connection in connections)


def test_session_is_lazy_and_closed_connection_is_not_replaced(monkeypatch: Any) -> None:
    settings = ApiSettings()
    calls: list[Connection] = []

    def connect(settings: Any) -> Connection:
        connection = Connection()
        calls.append(connection)
        return connection

    monkeypatch.setattr(chat, "connect", connect)
    with chat._request_snowflake_session(settings):
        assert calls == []
    with chat._request_snowflake_session(settings):
        with chat._chat_snowflake_connection(settings) as connection:
            connection.closed = True
        with pytest.raises(RuntimeError, match="closed"), chat._chat_snowflake_connection(settings):
            pass
    assert len(calls) == 1


def test_authorization_denial_closes_without_embedding_or_generation(monkeypatch: Any) -> None:
    connection = Connection()
    connection.authorized = False
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    with pytest.raises(ValueError, match="capability"):
        service(ApiSettings()).chat(
            KnowledgeChatRequest(
                message="study?",
                conversation_id="conversation",
                conversation_token="private-token",
            ),
            "r",
            "n",
        )
    assert connection.closed
    assert len(connection.calls) == 1 and "VERIFY" in connection.calls[0][0]


def test_optional_provenance_failure_rolls_back_then_persists(monkeypatch: Any) -> None:
    connection = Connection(failure="LOOKUP")
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    result = service(ApiSettings()).chat(KnowledgeChatRequest(message="study?"), "r", "n")
    assert result.status == "answered"
    assert connection.closed and connection.rollbacks == 1 and connection.commits == 2
    assert sum("PERSIST" in sql for sql, _ in connection.calls) == 2


def test_second_turn_failure_never_replays_first_turn(monkeypatch: Any) -> None:
    connection = Connection()
    original_cursor = connection.cursor

    def cursor() -> Cursor:
        result = original_cursor()
        original_execute = result.execute

        def execute(sql: str, params: Any) -> None:
            original_execute(sql, params)
            if "PERSIST" in sql and params[5] == "assistant":
                raise RuntimeError("second turn failure")

        result.execute = execute  # type: ignore[method-assign]
        return result

    monkeypatch.setattr(connection, "cursor", cursor)
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    result = service(ApiSettings()).chat(KnowledgeChatRequest(message="study?"), "r", "n")
    assert result.status == "evidence_unavailable"
    assert connection.closed and connection.rollbacks == 1
    assert [params[5] for sql, params in connection.calls if "PERSIST" in sql] == [
        "user",
        "assistant",
    ]


def test_existing_embedding_before_budget_boundary_is_explicit(monkeypatch: Any) -> None:
    events: list[str] = []
    connection = Connection()

    def connect(settings: Any) -> Connection:
        events.append("budget_connection")
        return connection

    class RecordingRetriever(Retriever):
        def search(self, message: str, request_id: str) -> Any:
            events.append("embedding_and_retrieval")
            return super().search(message, request_id)

    monkeypatch.setattr(chat, "connect", connect)
    instance = service(ApiSettings())
    instance._retriever = RecordingRetriever()
    result = instance.chat(KnowledgeChatRequest(message="study?"), "r", "n")
    assert result.status == "answered"
    # This pre-existing admission gap is deliberately not expanded in API #125.
    assert events == ["embedding_and_retrieval", "budget_connection"]


def test_cancellation_unwinds_connection_and_request_context(monkeypatch: Any) -> None:
    connection = Connection()
    settings = ApiSettings()
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    with (
        pytest.raises(KeyboardInterrupt),
        chat._request_snowflake_session(settings),
        chat._chat_snowflake_connection(settings),
    ):
        raise KeyboardInterrupt
    assert connection.closed and connection.rollbacks == 1
    assert chat._SNOWFLAKE_SESSION.get() is None


def test_copied_context_cannot_share_connection_with_another_thread(monkeypatch: Any) -> None:
    connection = Connection()
    settings = ApiSettings()
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    with chat._request_snowflake_session(settings):
        with chat._chat_snowflake_connection(settings):
            pass
        context = copy_context()

        def other_worker() -> None:
            with chat._chat_snowflake_connection(settings):
                pass

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(context.run, other_worker)
            with pytest.raises(RuntimeError, match="worker threads"):
                future.result(timeout=5)
    assert connection.closed

@pytest.mark.parametrize("autocommit", [None, False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_effective_autocommit_matches_connector_exit(
    monkeypatch: Any, autocommit: bool | None, fails: bool
) -> None:
    settings = ApiSettings()
    connection = Connection()
    if autocommit is not None:
        connection._session_parameters["AUTOCOMMIT"] = autocommit
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    original = RuntimeError("operation failed")
    with chat._request_snowflake_session(settings):
        if fails:
            with pytest.raises(RuntimeError) as caught, chat._chat_snowflake_connection(settings):
                raise original
            assert caught.value is original
        else:
            with chat._chat_snowflake_connection(settings):
                pass
    assert connection.commits == int(not fails and autocommit is not True)
    assert connection.rollbacks == int(fails and autocommit is not True)
    assert connection.closed
    assert chat._SNOWFLAKE_SESSION.get() is None


@pytest.mark.parametrize("transaction", ["commit", "rollback"])
def test_transaction_failure_still_closes_and_resets(
    monkeypatch: Any, transaction: str
) -> None:
    settings = ApiSettings()
    connection = Connection()
    failure = RuntimeError("transaction failed")

    def fail() -> None:
        raise failure

    monkeypatch.setattr(connection, transaction, fail)
    monkeypatch.setattr(chat, "connect", lambda settings: connection)
    with (
        pytest.raises(RuntimeError) as caught,
        chat._request_snowflake_session(settings),
        chat._chat_snowflake_connection(settings),
    ):
        if transaction == "rollback":
            raise ValueError("operation failed")
    assert caught.value is failure  # Connector baseline propagates transaction failure.
    assert connection.closed
    assert chat._SNOWFLAKE_SESSION.get() is None


@pytest.mark.parametrize("failure_stage", ["transport", "binding"])
def test_locked_cursor_failed_second_execute_does_not_log_stale_query(
    monkeypatch: Any, caplog: Any, failure_stage: str
) -> None:
    import logging
    from unittest.mock import MagicMock

    from snowflake.connector.cursor import SnowflakeCursor

    caplog.set_level(logging.INFO, logger=chat.__name__)
    connection = MagicMock()
    connection.is_closed.return_value = False
    connection.is_pyformat = False
    connection.log_max_query_length = 1000
    cursor = SnowflakeCursor(connection)
    failure = RuntimeError("private transport or binding failure")
    monkeypatch.setattr(cursor, "_init_result_and_meta", lambda data: None)
    helper = MagicMock(return_value={"success": True, "data": {"queryId": "first-call"}})
    monkeypatch.setattr(cursor, "_execute_helper", helper)
    chat._snowflake_execute(cursor, "CALL FIRST(?)", ("private",), "persist_user")
    assert cursor.sfqid == "first-call"
    if failure_stage == "transport":
        helper.side_effect = failure
    else:
        connection._process_params_qmarks.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        chat._snowflake_execute(cursor, "CALL SECOND(?)", ("private",), "persist_assistant")
    assert caught.value is failure
    assert cursor.sfqid == "first-call"  # Real connector reset retains this old ID.
    records = [r.context for r in caplog.records if r.msg == "knowledge_chat_snowflake_operation"]
    assert all("query_id" not in record for record in records)
    assert records[-1]["outcome"] == "failure"
    assert "private transport" not in caplog.text
