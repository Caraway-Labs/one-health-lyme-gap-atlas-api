"""Offline proof of the bounded read-session pool; not production latency evidence."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, get_ident
from typing import Any

import pytest
from fastapi.testclient import TestClient
from snowflake.connector import errors as sf_errors

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_contract import PublicQueryError
from lyme_gap_atlas_api.public_observations import SnowflakeObservationRepository
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError
from lyme_gap_atlas_api.snowflake_read_session import (
    ReadSessionPool,
    discard_reason,
    get_read_session_pool,
    read_connection,
)

CONNECT = "lyme_gap_atlas_api.snowflake_read_session.connect"


@pytest.fixture(autouse=True)
def _isolated_read_pool() -> None:
    get_read_session_pool().close_all()
    yield
    get_read_session_pool().close_all()


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, **_: Any) -> None:
        if self.connection.killed:
            raise RuntimeError("session expired")
        self.connection.statements.append(sql)

    def fetchone(self) -> tuple[int]:
        return (1,)


class FakeConnection:
    def __init__(self) -> None:
        self.owner = get_ident()
        self.closed = False
        self.killed = False  # server-side kill: is_closed() stays False
        self.close_calls = 0
        self.statements: list[str] = []

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _factory(monkeypatch: Any) -> list[FakeConnection]:
    connections: list[FakeConnection] = []

    def connect(_settings: Any) -> FakeConnection:
        connections.append(FakeConnection())
        return connections[-1]

    monkeypatch.setattr(CONNECT, connect)
    return connections


def _pool(**kwargs: Any) -> ReadSessionPool:
    kwargs.setdefault("reaper_interval_seconds", None)
    return ReadSessionPool(**kwargs)


def test_two_checkouts_on_one_thread_share_one_connect(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    settings = ApiSettings()
    with read_connection(settings):
        pass
    with read_connection(settings):
        pass
    assert len(connections) == 1
    assert connections[0].close_calls == 0


def test_settings_defaults_are_safe() -> None:
    settings = ApiSettings()
    assert 1 <= settings.read_pool_max_size <= 64
    assert settings.read_pool_checkout_timeout_seconds > 0
    assert settings.read_pool_validate_after_idle_seconds < settings.read_pool_idle_seconds


def test_idle_eviction_reconnects(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    clock = Clock()
    pool = _pool(idle_seconds=10.0, clock=clock)
    settings = ApiSettings()
    pool.checkout(settings)
    pool.checkin()
    clock.now = 11.0
    pool.checkout(settings)
    assert len(connections) == 2
    assert connections[0].close_calls == 1


def test_reaper_evicts_idle_sessions(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    clock = Clock()
    pool = _pool(idle_seconds=10.0, clock=clock)
    pool.checkout(ApiSettings())
    pool.checkin()
    assert pool.reap_idle() == 0
    clock.now = 10.5
    assert pool.reap_idle() == 1
    assert connections[0].closed
    assert pool.stats() == {"idle": 0, "in_use": 0, "creating": 0}


def test_reaper_thread_runs_and_stops_on_close_all(monkeypatch: Any) -> None:
    _factory(monkeypatch)
    pool = ReadSessionPool(idle_seconds=10.0, reaper_interval_seconds=0.01)
    pool.checkout(ApiSettings())
    pool.checkin()
    reaper = pool._reaper
    assert reaper is not None and reaper.is_alive()
    pool.close_all()
    reaper.join(timeout=2)
    assert not reaper.is_alive()


def test_closed_connection_is_replaced(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    pool = _pool()
    settings = ApiSettings()
    first = pool.checkout(settings)
    pool.checkin()
    first.closed = True  # type: ignore[attr-defined]
    second = pool.checkout(settings)
    assert len(connections) == 2
    assert second is connections[1]


def test_killed_session_is_replaced_on_checkout_without_failing(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    clock = Clock()
    pool = _pool(clock=clock, validate_after_idle_seconds=30.0)
    settings = ApiSettings()
    pool.checkout(settings)
    pool.checkin()
    connections[0].killed = True
    clock.now = 31.0  # past the idle threshold: server round trip happens
    replacement = pool.checkout(settings)
    assert replacement is connections[1]
    assert connections[0].close_calls == 1
    assert connections[1].statements == []  # the request's own statements never replayed
    pool.checkin()
    assert pool.stats() == {"idle": 1, "in_use": 0, "creating": 0}


def test_recently_used_connection_skips_server_round_trip(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    clock = Clock()
    pool = _pool(clock=clock, validate_after_idle_seconds=30.0)
    settings = ApiSettings()
    pool.checkout(settings)
    pool.checkin()
    clock.now = 5.0
    pool.checkout(settings)
    assert connections[0].statements == []
    pool.checkin()
    clock.now = 40.0
    pool.checkout(settings)
    assert connections[0].statements == ["SELECT 1"]


def test_exception_drops_session(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    settings = ApiSettings()
    with pytest.raises(RuntimeError), read_connection(settings):
        raise RuntimeError("query failed")
    assert get_read_session_pool().stats()["in_use"] == 0
    with read_connection(settings):
        pass
    assert len(connections) == 2
    assert connections[0].close_calls == 1


def _wrapped_unavailable() -> AtlasDataUnavailableError:
    try:
        try:
            raise sf_errors.OperationalError("network down")
        except sf_errors.OperationalError as cause:
            raise AtlasDataUnavailableError("unavailable") from cause
    except AtlasDataUnavailableError as exc:
        return exc


def test_public_query_error_keeps_connection(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    settings = ApiSettings()
    with pytest.raises(PublicQueryError), read_connection(settings) as first:
        raise PublicQueryError("QUERY_TOO_BROAD", "too broad")
    assert get_read_session_pool().stats() == {"idle": 1, "in_use": 0, "creating": 0}
    with read_connection(settings) as second:
        pass
    assert second is first
    assert len(connections) == 1
    assert connections[0].close_calls == 0


def test_own_unavailable_error_keeps_connection(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    settings = ApiSettings()
    with pytest.raises(AtlasDataUnavailableError), read_connection(settings):
        raise AtlasDataUnavailableError("no active batch")
    with read_connection(settings):
        pass
    assert len(connections) == 1


@pytest.mark.parametrize(
    "error",
    [
        sf_errors.OperationalError("network down"),
        sf_errors.DatabaseError("db"),
        sf_errors.ProgrammingError("Authentication token has expired", errno=390114),
        sf_errors.ProgrammingError("SQL execution was cancelled by the client", errno=604),
        TimeoutError("statement timed out"),
        asyncio.CancelledError(),
        KeyboardInterrupt(),
        RuntimeError("unknown"),
        _wrapped_unavailable(),
    ],
)
def test_connection_level_errors_discard(monkeypatch: Any, error: BaseException) -> None:
    connections = _factory(monkeypatch)
    settings = ApiSettings()
    with pytest.raises(type(error)), read_connection(settings):
        raise error
    assert get_read_session_pool().stats()["idle"] == 0
    assert connections[0].close_calls == 1
    with read_connection(settings):
        pass
    assert len(connections) == 2


def test_sql_programming_error_keeps_open_connection(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    settings = ApiSettings()
    error = sf_errors.ProgrammingError("SQL compilation error: syntax", errno=1003)
    with pytest.raises(sf_errors.ProgrammingError), read_connection(settings):
        raise error
    with read_connection(settings):
        pass
    assert len(connections) == 1


def test_discard_reason_values_and_closed_connection() -> None:
    assert discard_reason(PublicQueryError("X", "x")) is None
    assert discard_reason(sf_errors.DataError("bad value")) is None
    assert discard_reason(TimeoutError()) == "timeout"
    assert discard_reason(asyncio.CancelledError()) == "cancelled_or_interrupted"
    assert discard_reason(sf_errors.OperationalError("x")) == "connector_error"
    assert discard_reason(_wrapped_unavailable()) == "wrapped_error"
    assert discard_reason(RuntimeError("x")) == "unknown_error"
    closed = FakeConnection()
    closed.closed = True
    assert discard_reason(PublicQueryError("X", "x"), closed) == "connection_closed"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("error", "raised", "kept"),
    [
        (PublicQueryError("QUERY_TOO_BROAD", "too broad"), PublicQueryError, True),
        # Connector errors are wrapped for callers, as before this change.
        (sf_errors.OperationalError("network down"), AtlasDataUnavailableError, False),
        (TimeoutError(), TimeoutError, False),
    ],
)
def test_request_session_close_on_exit_error_honors_policy(
    monkeypatch: Any, error: Exception, raised: type[Exception], kept: bool
) -> None:
    connections = _factory(monkeypatch)
    settings = ApiSettings()
    repository = SnowflakeObservationRepository(settings)
    with (
        pytest.raises(raised),
        repository.request_session(close_on_exit_error=True),
    ):
        raise error
    assert (connections[0].close_calls == 0) is kept
    assert get_read_session_pool().stats()["idle"] == (1 if kept else 0)


def test_drop_current_is_idempotent_and_frees_slot(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    pool = _pool(max_size=1)
    settings = ApiSettings()
    pool.checkout(settings)
    pool.drop_current()
    pool.drop_current()
    pool.checkin()
    pool.checkout(settings)
    assert len(connections) == 2
    assert connections[0].close_calls == 1


def test_connect_failure_releases_slot(monkeypatch: Any) -> None:
    attempts = []

    def connect(_settings: Any) -> FakeConnection:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("login failed")
        return FakeConnection()

    monkeypatch.setattr(CONNECT, connect)
    pool = _pool(max_size=1, checkout_timeout_seconds=0.05)
    with pytest.raises(RuntimeError):
        pool.checkout(ApiSettings())
    pool.checkout(ApiSettings())
    assert pool.stats()["in_use"] == 1


def test_concurrent_threads_get_distinct_connections(monkeypatch: Any) -> None:
    barrier = Barrier(2)
    connections: list[FakeConnection] = []

    def connect(_settings: Any) -> FakeConnection:
        connection = FakeConnection()
        connections.append(connection)
        barrier.wait(timeout=5)
        return connection

    monkeypatch.setattr(CONNECT, connect)
    settings = ApiSettings()

    def borrow(_: int) -> None:
        with read_connection(settings):
            pass

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(borrow, range(2)))
    assert len(connections) == 2


def test_pool_never_exceeds_max_under_concurrent_load(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    pool = _pool(max_size=3, checkout_timeout_seconds=10.0)
    settings = ApiSettings()
    lock = threading.Lock()
    active = 0
    peak = 0
    borrowed: set[int] = set()

    def work(_: int) -> None:
        nonlocal active, peak
        connection = pool.checkout(settings)
        try:
            with lock:
                active += 1
                peak = max(peak, active)
                assert id(connection) not in borrowed  # never shared concurrently
                borrowed.add(id(connection))
            threading.Event().wait(0.01)
            with lock:
                active -= 1
                borrowed.discard(id(connection))
        finally:
            pool.checkin()

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(work, range(60)))
    assert peak <= 3
    assert len(connections) <= 3
    assert pool.stats()["in_use"] == 0


def test_exhaustion_times_out_as_unavailable_error(monkeypatch: Any) -> None:
    _factory(monkeypatch)
    pool = _pool(max_size=1, checkout_timeout_seconds=0.05)
    settings = ApiSettings()
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        pool.checkout(settings)
        held.set()
        release.wait(5)
        pool.checkin()

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(5)
    try:
        with pytest.raises(AtlasDataUnavailableError):
            pool.checkout(settings)
    finally:
        release.set()
        thread.join(5)
    pool.checkout(settings)  # capacity returns once the holder checks in


def test_waiter_is_served_when_a_connection_is_returned(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    pool = _pool(max_size=1, checkout_timeout_seconds=5.0)
    settings = ApiSettings()
    first = pool.checkout(settings)
    results: list[Any] = []

    def waiter() -> None:
        results.append(pool.checkout(settings))
        pool.checkin()

    # The waiter runs on another thread; release the main thread's lease afterwards.
    thread = threading.Thread(target=waiter)
    thread.start()
    threading.Event().wait(0.05)
    assert results == []
    pool.checkin()
    thread.join(5)
    assert results == [first]
    assert len(connections) == 1


def test_read_connection_exhaustion_maps_to_unavailable(monkeypatch: Any) -> None:
    _factory(monkeypatch)
    pool = get_read_session_pool()
    settings = ApiSettings(read_pool_max_size=1, read_pool_checkout_timeout_seconds=0.05)
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with read_connection(settings):
            held.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(5)
    try:
        with pytest.raises(AtlasDataUnavailableError), read_connection(settings):
            pass
    finally:
        release.set()
        thread.join(5)
    assert pool.stats()["in_use"] == 0


def test_close_all_closes_idle_and_borrowed_on_return(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    pool = _pool(max_size=2)
    settings = ApiSettings()
    ready = threading.Event()
    release = threading.Event()

    def borrower() -> None:
        pool.checkout(settings)
        ready.set()
        release.wait(5)
        pool.checkin()

    thread = threading.Thread(target=borrower)
    thread.start()
    assert ready.wait(5)
    pool.checkout(settings)
    pool.checkin()
    pool.close_all()
    assert connections[1].closed  # the idle one is closed immediately
    release.set()
    thread.join(5)
    assert all(connection.closed for connection in connections)
    assert pool.stats() == {"idle": 0, "in_use": 0, "creating": 0}


def test_lifespan_shutdown_closes_pool(monkeypatch: Any) -> None:
    connections = _factory(monkeypatch)
    with TestClient(create_app(ApiSettings())):
        with read_connection(ApiSettings()):
            pass
        assert not connections[0].closed
    assert connections[0].closed
