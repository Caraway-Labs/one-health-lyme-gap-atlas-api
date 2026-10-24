"""Write-path pool: success-only reuse, discard on doubt, no replay (API #325)."""

from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api import feedback as feedback_module
from lyme_gap_atlas_api import snowflake_write_session as write_pool_module
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.feedback import FeedbackStoreError, SnowflakeFeedbackStore
from lyme_gap_atlas_api.snowflake_write_session import WritePoolExhaustedError, WriteSessionPool


class Cursor:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.result: Any = None

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def execute(self, sql: str, params: Any = None, **kwargs: Any) -> None:
        self.connection.executed.append(sql)
        if sql == "SELECT 1":
            if self.connection.validation_fails:
                raise RuntimeError("session gone")
            return
        if self.connection.fail_call:
            raise self.connection.fail_call
        self.result = (
            {
                "status": "created",
                "feedback_id": str(uuid4()),
                "received_at": "2026-10-24 10:00:00.000 -0700",
            },
        )

    def fetchone(self) -> Any:
        return self.result


class Connection:
    def __init__(self, *, fail_call: BaseException | None = None) -> None:
        self.closed = False
        self.fail_call = fail_call
        self.validation_fails = False
        self.executed: list[str] = []

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def cursor(self) -> Cursor:
        return Cursor(self)


class Factory:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.connections: list[Connection] = []

    def __call__(self, settings: Any) -> Connection:
        connection = Connection(**self.kwargs)
        self.connections.append(connection)
        return connection


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


SETTINGS = ApiSettings(write_pool_max_size=2, write_pool_checkout_timeout_seconds=0.05)


def _submit(store: SnowflakeFeedbackStore) -> Any:
    return store.submit(
        submission_token=str(uuid4()),
        payload_fingerprint="f" * 64,
        category="general",
        message="message",
        route_id="overview",
        context_json=None,
        app_version="v",
        schema_version="1",
        contact_email=None,
        account_id=None,
    )


def test_settings_defaults() -> None:
    settings = ApiSettings()
    assert settings.write_pool_max_size == 4
    assert settings.write_pool_idle_seconds == 120.0
    assert settings.write_pool_validate_after_idle_seconds == 30.0
    assert settings.write_pool_max_size < settings.read_pool_max_size


def test_borrow_reuses_connection_after_success() -> None:
    pool, factory = WriteSessionPool(), Factory()
    with pool.borrow(SETTINGS, factory) as first:
        pass
    with pool.borrow(SETTINGS, factory) as second:
        pass
    assert first is second and len(factory.connections) == 1
    assert not first.closed and pool.stats()["idle"] == 1


@pytest.mark.parametrize("failure", [RuntimeError("x"), TimeoutError(), KeyboardInterrupt()])
def test_any_exception_discards_and_next_borrow_reconnects(failure: BaseException) -> None:
    pool, factory = WriteSessionPool(), Factory()
    with pytest.raises(type(failure)), pool.borrow(SETTINGS, factory):
        raise failure
    assert factory.connections[0].closed
    assert pool.stats() == {"idle": 0, "in_use": 0, "creating": 0}
    with pool.borrow(SETTINGS, factory) as connection:
        assert connection is factory.connections[1]


def test_pool_is_separate_from_read_pool() -> None:
    from lyme_gap_atlas_api.snowflake_read_session import get_read_session_pool

    assert write_pool_module.get_write_session_pool() is not get_read_session_pool()


def test_closed_idle_connection_is_replaced_locally() -> None:
    pool, factory = WriteSessionPool(), Factory()
    with pool.borrow(SETTINGS, factory) as first:
        pass
    first.close()
    with pool.borrow(SETTINGS, factory) as second:
        pass
    assert second is factory.connections[1]
    assert first.executed == []  # local is_closed check needs no round trip


def test_validation_only_after_idle_threshold_and_before_caller_statement() -> None:
    clock = Clock()
    pool, factory = WriteSessionPool(clock=clock), Factory()
    with pool.borrow(SETTINGS, factory) as first:
        pass
    with pool.borrow(SETTINGS, factory):
        pass
    assert first.executed == []  # fresh: no SELECT 1
    clock.now += 31  # beyond validate-after (30s) but within idle (120s)
    with pool.borrow(SETTINGS, factory) as again:
        assert again is first and first.executed == ["SELECT 1"]
    clock.now += 31
    first.validation_fails = True
    with pool.borrow(SETTINGS, factory) as replacement:
        assert replacement is factory.connections[1]
    assert first.closed


def test_expired_idle_connection_is_closed_not_reused() -> None:
    clock = Clock()
    pool, factory = WriteSessionPool(clock=clock), Factory()
    with pool.borrow(SETTINGS, factory) as first:
        pass
    clock.now += 121
    with pool.borrow(SETTINGS, factory) as second:
        pass
    assert first.closed and second is not first


def test_exhaustion_raises_after_bounded_wait_without_connecting() -> None:
    pool, factory = WriteSessionPool(), Factory()
    with (
        pool.borrow(SETTINGS, factory),
        pool.borrow(SETTINGS, factory),
        pytest.raises(WritePoolExhaustedError),
    ):
        pool.checkout(SETTINGS, factory)
    assert len(factory.connections) == 2


def test_connect_failure_releases_reserved_slot() -> None:
    pool = WriteSessionPool()

    def boom(settings: Any) -> Connection:
        raise RuntimeError("login failed")

    for _ in range(3):
        with pytest.raises(RuntimeError), pool.borrow(SETTINGS, boom):
            pass
    assert pool.stats() == {"idle": 0, "in_use": 0, "creating": 0}


def test_waiter_gets_connection_released_by_other_thread() -> None:
    pool, factory = WriteSessionPool(), Factory()
    settings = ApiSettings(write_pool_max_size=1, write_pool_checkout_timeout_seconds=5)
    held = pool.checkout(settings, factory)
    got: list[Any] = []
    thread = threading.Thread(target=lambda: got.append(pool.checkout(settings, factory)))
    thread.start()
    pool.release(held)
    thread.join(5)
    assert got == [held] and len(factory.connections) == 1


def test_close_all_closes_idle_and_borrowed_on_return() -> None:
    pool, factory = WriteSessionPool(), Factory()
    with pool.borrow(SETTINGS, factory) as idle:
        pass
    with pool.borrow(SETTINGS, factory) as borrowed:  # reuses idle
        assert borrowed is idle
        pool.close_all()
        assert not borrowed.closed
    assert borrowed.closed and pool.stats()["idle"] == 0
    with pool.borrow(SETTINGS, factory) as fresh:
        assert fresh is not borrowed


def test_lifespan_shutdown_closes_write_pool(
    _fresh_write_session_pool: WriteSessionPool,
) -> None:
    from lyme_gap_atlas_api.app import create_app

    factory = Factory()
    with _fresh_write_session_pool.borrow(SETTINGS, factory):
        pass
    with TestClient(create_app(settings=ApiSettings())):
        assert not factory.connections[0].closed
    assert factory.connections[0].closed


# --- feedback store wiring -------------------------------------------------------------


def test_feedback_submits_reuse_one_login(monkeypatch: Any) -> None:
    factory = Factory()
    monkeypatch.setattr(feedback_module, "connect", factory)
    store = SnowflakeFeedbackStore(SETTINGS)
    assert _submit(store).status == "created"
    assert _submit(store).status == "created"
    assert len(factory.connections) == 1  # was one login per submit before #325
    calls = [s for s in factory.connections[0].executed if s.startswith("CALL")]
    assert len(calls) == 2


@pytest.mark.parametrize("failure", [RuntimeError("private detail"), TimeoutError()])
def test_feedback_failure_is_uncertain_discarded_and_never_replayed(
    monkeypatch: Any, failure: BaseException
) -> None:
    factory = Factory(fail_call=failure)
    monkeypatch.setattr(feedback_module, "connect", factory)
    store = SnowflakeFeedbackStore(SETTINGS)
    with pytest.raises(FeedbackStoreError) as raised:
        _submit(store)
    assert "private detail" not in str(raised.value)
    assert len(factory.connections) == 1  # no second connection, so no replay
    assert factory.connections[0].closed
    assert sum(s.startswith("CALL") for s in factory.connections[0].executed) == 1
    factory.kwargs = {}
    assert _submit(store).status == "created"  # next call reconnects cleanly
    assert len(factory.connections) == 2


def test_feedback_pool_exhaustion_maps_to_store_error(monkeypatch: Any) -> None:
    factory = Factory()
    monkeypatch.setattr(feedback_module, "connect", factory)
    settings = ApiSettings(write_pool_max_size=1, write_pool_checkout_timeout_seconds=0.05)
    pool = write_pool_module.get_write_session_pool()
    held = pool.checkout(settings, factory)
    with pytest.raises(FeedbackStoreError):
        _submit(SnowflakeFeedbackStore(settings))
    pool.release(held)
    assert len(factory.connections) == 1


def test_feedback_redact_and_export_use_write_pool(monkeypatch: Any) -> None:
    factory = Factory()
    monkeypatch.setattr(feedback_module, "connect", factory)
    store = SnowflakeFeedbackStore(SETTINGS)
    _submit(store)
    with pytest.raises(FeedbackStoreError):  # fake returns a "created" payload: not "ok"
        store.list_export_rows("account")
    # A well-formed procedure response that fails validation is not a connection fault.
    assert len(factory.connections) == 1 and not factory.connections[0].closed
