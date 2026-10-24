"""Offline proof of thread-local read session reuse; not production latency evidence."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, get_ident
from typing import Any

import pytest

from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.snowflake_read_session import (
    ReadSessionPool,
    get_read_session_pool,
    read_connection,
)


@pytest.fixture(autouse=True)
def _isolated_read_pool() -> None:
    get_read_session_pool().close_all()
    yield
    get_read_session_pool().close_all()


class FakeConnection:
    def __init__(self) -> None:
        self.owner = get_ident()
        self.closed = False
        self.close_calls = 0

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1


def test_two_checkouts_on_one_thread_share_one_connect(monkeypatch: Any) -> None:
    connections: list[FakeConnection] = []

    def connect(_settings: Any) -> FakeConnection:
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr("lyme_gap_atlas_api.snowflake_read_session.connect", connect)
    settings = ApiSettings()
    with read_connection(settings):
        pass
    with read_connection(settings):
        pass
    assert len(connections) == 1
    assert connections[0].close_calls == 0


def test_idle_eviction_reconnects(monkeypatch: Any) -> None:
    connections: list[FakeConnection] = []
    clock = [0.0]

    def connect(_settings: Any) -> FakeConnection:
        connections.append(FakeConnection())
        return connections[-1]

    monkeypatch.setattr("lyme_gap_atlas_api.snowflake_read_session.connect", connect)
    monkeypatch.setattr(
        "lyme_gap_atlas_api.snowflake_read_session.time.monotonic",
        lambda: clock[0],
    )
    pool = ReadSessionPool(idle_seconds=10.0)
    settings = ApiSettings()
    pool.checkout(settings)
    clock[0] = 11.0
    pool.checkout(settings)
    assert len(connections) == 2
    assert connections[0].close_calls == 1


def test_closed_connection_is_replaced(monkeypatch: Any) -> None:
    connections: list[FakeConnection] = []

    def connect(_settings: Any) -> FakeConnection:
        connections.append(FakeConnection())
        return connections[-1]

    monkeypatch.setattr("lyme_gap_atlas_api.snowflake_read_session.connect", connect)
    pool = ReadSessionPool()
    settings = ApiSettings()
    first = pool.checkout(settings)
    first.closed = True
    second = pool.checkout(settings)
    assert len(connections) == 2
    assert second is connections[1]


def test_exception_drops_session(monkeypatch: Any) -> None:
    connections: list[FakeConnection] = []

    def connect(_settings: Any) -> FakeConnection:
        connections.append(FakeConnection())
        return connections[-1]

    monkeypatch.setattr("lyme_gap_atlas_api.snowflake_read_session.connect", connect)
    settings = ApiSettings()
    with pytest.raises(RuntimeError), read_connection(settings):
        raise RuntimeError("query failed")
    with read_connection(settings):
        pass
    assert len(connections) == 2
    assert connections[0].close_calls == 1


def test_concurrent_threads_get_distinct_connections(monkeypatch: Any) -> None:
    barrier = Barrier(2)
    connections: list[FakeConnection] = []

    def connect(_settings: Any) -> FakeConnection:
        connection = FakeConnection()
        connections.append(connection)
        barrier.wait(timeout=5)
        return connection

    monkeypatch.setattr("lyme_gap_atlas_api.snowflake_read_session.connect", connect)
    settings = ApiSettings()
    def borrow(_: int) -> None:
        with read_connection(settings):
            pass

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(borrow, range(2)))
    assert len(connections) == 2
