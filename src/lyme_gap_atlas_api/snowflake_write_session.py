"""Small, separate Snowflake connection pool for write paths (feedback, Ask Atlas persistence).

Never shared with the read pool. A write connection is returned to the pool only after the
whole borrowed block finished successfully; any exception, cancellation, timeout or doubt
discards (closes) it, and a procedure call is never retried or replayed on another
connection. Liveness is checked before the first caller statement only.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from time import perf_counter
from typing import Any

from snowflake.connector import SnowflakeConnection

from .dependency_telemetry import connect as default_connect
from .snowflake_pool_metrics import WRITE_METRICS

DEFAULT_MAX_SIZE = 4
DEFAULT_CHECKOUT_TIMEOUT_SECONDS = 5.0
DEFAULT_IDLE_SECONDS = 120.0
DEFAULT_VALIDATE_AFTER_IDLE_SECONDS = 30.0
_VALIDATION_TIMEOUT_SECONDS = 5


class WritePoolExhaustedError(RuntimeError):
    """No write connection became available within the checkout timeout."""


class _Lease:
    def __init__(
        self, connection: SnowflakeConnection, settings: object, now: float, epoch: int
    ) -> None:
        self.connection = connection
        self.settings = settings
        self.last_used = now
        self.epoch = epoch


class WriteSessionPool:
    """Bounded pool of write connections; keep policy is success-only."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._idle: deque[_Lease] = deque()
        self._in_use: dict[int, _Lease] = {}  # id(connection) -> lease
        self._reserved = 0  # slots held while a connection is being created
        self._epoch = 0

    @staticmethod
    def _limits(settings: object) -> tuple[int, float, float, float]:
        # Plain SnowflakeSettings (injected stores, tests) carry no pool fields.
        return (
            int(getattr(settings, "write_pool_max_size", DEFAULT_MAX_SIZE)),
            float(
                getattr(
                    settings,
                    "write_pool_checkout_timeout_seconds",
                    DEFAULT_CHECKOUT_TIMEOUT_SECONDS,
                )
            ),
            float(getattr(settings, "write_pool_idle_seconds", DEFAULT_IDLE_SECONDS)),
            float(
                getattr(
                    settings,
                    "write_pool_validate_after_idle_seconds",
                    DEFAULT_VALIDATE_AFTER_IDLE_SECONDS,
                )
            ),
        )

    def checkout(
        self, settings: Any, connect_fn: Callable[[Any], SnowflakeConnection] = default_connect
    ) -> SnowflakeConnection:
        """Lend a live connection exclusively, waiting a bounded time for a slot."""
        started = perf_counter()
        max_size, timeout, idle_seconds, validate_after = self._limits(settings)
        exhausted = False
        deadline = self._clock() + timeout
        stale: list[_Lease] = []
        lease: _Lease | None = None
        try:
            with self._cond:
                while True:
                    now = self._clock()
                    for item in list(self._idle):
                        if now - item.last_used > idle_seconds or item.settings is not settings:
                            self._idle.remove(item)
                            stale.append(item)
                    if self._idle:
                        lease = self._idle.pop()  # most recently used first
                        self._in_use[id(lease.connection)] = lease
                        break
                    if len(self._in_use) + self._reserved < max_size:
                        self._reserved += 1
                        break
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        exhausted = True
                        raise WritePoolExhaustedError(
                            "Snowflake write connection pool is exhausted"
                        )
                    self._cond.wait(remaining)
        finally:
            for item in stale:
                self._close(item.connection)
                if item.settings is not settings:
                    WRITE_METRICS.record_discard("settings_changed")
                else:
                    WRITE_METRICS.record_eviction()
            if exhausted:
                self._observe("exhausted", started, max_size)
        if lease is not None:
            if self._is_healthy(lease, validate_after):
                self._observe("reused", started, max_size)
                return lease.connection
            # Dead session: swap its slot for a reservation, then replace it.
            with self._cond:
                self._in_use.pop(id(lease.connection), None)
                self._reserved += 1
            self._close(lease.connection)
            WRITE_METRICS.record_discard("health_check_failed")
        connection = self._create(settings, connect_fn)
        self._observe("created" if lease is None else "replaced_dead", started, max_size)
        return connection

    def _observe(self, outcome: str, started: float, max_size: int) -> None:
        with suppress(Exception):
            sizes = self.stats()
            WRITE_METRICS.record_checkout(
                outcome,
                perf_counter() - started,
                in_use=sizes["in_use"],
                idle=sizes["idle"],
                max_size=max_size,
            )

    def _create(
        self, settings: Any, connect_fn: Callable[[Any], SnowflakeConnection]
    ) -> SnowflakeConnection:
        try:
            connection = connect_fn(settings)
        except BaseException:
            with self._cond:
                self._reserved -= 1
                self._cond.notify()
            raise
        with self._cond:
            self._reserved -= 1
            self._in_use[id(connection)] = _Lease(connection, settings, self._clock(), self._epoch)
        return connection

    def _is_healthy(self, lease: _Lease, validate_after: float) -> bool:
        """Local check first; a server round trip only after the idle threshold.

        Runs before any caller statement, so no write is ever replayed by validation.
        """
        connection = lease.connection
        try:
            if connection.is_closed():
                return False
            if self._clock() - lease.last_used > validate_after:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1", timeout=_VALIDATION_TIMEOUT_SECONDS)
                    cursor.fetchone()
        except Exception:
            return False
        return True

    def release(self, connection: SnowflakeConnection) -> None:
        """Return a connection after a fully successful borrowed block."""
        with self._cond:
            lease = self._in_use.pop(id(connection), None)
            if lease is None:
                return
            keep = lease.epoch == self._epoch and not self._is_closed(connection)
            if keep:
                lease.last_used = self._clock()
                self._idle.append(lease)
            self._cond.notify()
        if not keep:
            self._close(connection)
            WRITE_METRICS.record_discard(
                "stale_epoch" if lease.epoch != self._epoch else "connection_closed"
            )

    def discard(self, connection: SnowflakeConnection, reason: str = "borrow_error") -> None:
        """Close a connection after any error or doubt; safe to call repeatedly."""
        with self._cond:
            tracked = self._in_use.pop(id(connection), None) is not None
            if tracked:
                self._cond.notify()
        self._close(connection)
        if tracked:
            WRITE_METRICS.record_discard(reason)

    @contextmanager
    def borrow(
        self, settings: Any, connect_fn: Callable[[Any], SnowflakeConnection] = default_connect
    ) -> Iterator[SnowflakeConnection]:
        connection = self.checkout(settings, connect_fn)
        try:
            yield connection
        except BaseException:
            self.discard(connection)
            raise
        self.release(connection)

    def close_all(self) -> None:
        """Close every idle connection; borrowed ones are closed when released."""
        with self._cond:
            self._epoch += 1
            idle = list(self._idle)
            self._idle.clear()
            self._cond.notify_all()
        for item in idle:
            self._close(item.connection)

    def stats(self) -> dict[str, int]:
        with self._cond:
            return {
                "idle": len(self._idle),
                "in_use": len(self._in_use),
                "creating": self._reserved,
            }

    @staticmethod
    def _is_closed(connection: SnowflakeConnection) -> bool:
        try:
            return bool(connection.is_closed())
        except Exception:
            return True

    @staticmethod
    def _close(connection: SnowflakeConnection) -> None:
        try:
            if not connection.is_closed():
                connection.close()
        except Exception:
            pass


_POOL = WriteSessionPool()


def get_write_session_pool() -> WriteSessionPool:
    return _POOL
