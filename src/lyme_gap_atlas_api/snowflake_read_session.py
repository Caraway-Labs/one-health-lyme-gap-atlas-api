"""Bounded, health-checked Snowflake read connection pool for public read paths."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING

from snowflake.connector import SnowflakeConnection
from snowflake.connector import errors as sf_errors

from .dependency_telemetry import connect
from .public_contract import PublicQueryError
from .repository import AtlasDataUnavailableError

if TYPE_CHECKING:
    from .config import ApiSettings

DEFAULT_IDLE_SECONDS = 300.0
_VALIDATION_TIMEOUT_SECONDS = 5

# Connector errnos/phrases meaning the statement was cancelled or the session/token is gone;
# the connector reports some of these as ProgrammingError, so they must still discard.
_SESSION_LOSS_ERRNOS = frozenset({604, 390111, 390114, 390195, 390318})
_SESSION_LOSS_PHRASES = ("timeout", "timed out", "cancel", "session", "token", "expired")


class _Lease:
    """A pooled connection; ``last_used`` is the last check-in (or creation) time."""

    def __init__(self, connection: SnowflakeConnection, now: float, epoch: int) -> None:
        self.connection = connection
        self.last_used = now
        self.epoch = epoch
        self.depth = 1


class ReadSessionPool:
    """Bounded pool of read connections; not for write paths.

    A connection is lent to one thread at a time (the connector is thread-confined),
    returned on a successful context exit and on errors ``discard_reason`` classifies as
    benign, and closed on any other error. Limits default to
    the checkout-time ``ApiSettings`` unless overridden in the constructor.
    """

    def __init__(
        self,
        *,
        max_size: int | None = None,
        checkout_timeout_seconds: float | None = None,
        idle_seconds: float | None = None,
        validate_after_idle_seconds: float | None = None,
        reaper_interval_seconds: float | None = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_size = max_size
        self._checkout_timeout = checkout_timeout_seconds
        self._idle_seconds = idle_seconds
        self._validate_after = validate_after_idle_seconds
        self._reaper_interval = reaper_interval_seconds
        self._clock = clock
        self._cond = threading.Condition()
        self._idle: deque[_Lease] = deque()
        self._in_use: dict[int, _Lease] = {}  # thread id -> lease
        self._reserved = 0  # slots held while a connection is being created
        self._epoch = 0
        self._observed_idle = DEFAULT_IDLE_SECONDS  # last idle limit seen at checkout
        self._reaper: threading.Thread | None = None
        self._reaper_stop = threading.Event()

    def _limits(self, settings: ApiSettings) -> tuple[int, float, float, float]:
        return (
            self._max_size if self._max_size is not None else settings.read_pool_max_size,
            self._checkout_timeout
            if self._checkout_timeout is not None
            else settings.read_pool_checkout_timeout_seconds,
            self._idle_seconds
            if self._idle_seconds is not None
            else settings.read_pool_idle_seconds,
            self._validate_after
            if self._validate_after is not None
            else settings.read_pool_validate_after_idle_seconds,
        )

    def checkout(self, settings: ApiSettings) -> SnowflakeConnection:
        """Lend a healthy connection to this thread, waiting a bounded time for one."""
        thread_id = threading.get_ident()
        with self._cond:
            held = self._in_use.get(thread_id)
            if held is not None:  # re-entrant use on the same thread shares the lease
                held.depth += 1
                return held.connection
        max_size, timeout, idle_seconds, validate_after = self._limits(settings)
        self._observed_idle = idle_seconds
        deadline = self._clock() + timeout
        stale: list[_Lease] = []
        lease: _Lease | None = None
        try:
            with self._cond:
                while True:
                    stale.extend(self._pop_expired(idle_seconds))
                    if self._idle:
                        lease = self._idle.pop()  # most recently used first
                        self._in_use[thread_id] = lease
                        break
                    if len(self._in_use) + self._reserved < max_size:
                        self._reserved += 1
                        break
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise AtlasDataUnavailableError(
                            "Snowflake read connection pool is exhausted"
                        )
                    self._cond.wait(remaining)
        finally:
            for item in stale:
                self._close(item.connection)
        if lease is not None:
            if self._is_healthy(lease, validate_after):
                return lease.connection
            # Dead session: swap its slot for a reservation, then replace it.
            with self._cond:
                self._in_use.pop(thread_id, None)
                self._reserved += 1
            self._close(lease.connection)
        return self._create(settings, thread_id)

    def _create(self, settings: ApiSettings, thread_id: int) -> SnowflakeConnection:
        """The single place a new pooled connection is created."""
        try:
            connection = connect(settings)
        except BaseException:
            with self._cond:
                self._reserved -= 1
                self._cond.notify()
            raise
        with self._cond:
            self._reserved -= 1
            self._in_use[thread_id] = _Lease(connection, self._clock(), self._epoch)
        self._start_reaper()
        return connection

    def _is_healthy(self, lease: _Lease, validate_after: float) -> bool:
        """Cheap local check first; a server round trip only after an idle threshold.

        Runs before the request's own statements, so no user statement is ever replayed.
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

    def checkin(self) -> None:
        """Return this thread's connection to the pool (single check-in path)."""
        thread_id = threading.get_ident()
        with self._cond:
            lease = self._in_use.get(thread_id)
            if lease is None:
                return
            lease.depth -= 1
            if lease.depth > 0:
                return
            del self._in_use[thread_id]
            keep = lease.epoch == self._epoch and not self._is_closed(lease.connection)
            if keep:
                lease.last_used = self._clock()
                self._idle.append(lease)
            self._cond.notify()
        if not keep:
            self._close(lease.connection)

    def drop_current(self) -> None:
        """Discard (close) this thread's connection; safe to call repeatedly."""
        with self._cond:
            lease = self._in_use.pop(threading.get_ident(), None)
            if lease is not None:
                self._cond.notify()
        if lease is not None:
            self._close(lease.connection)

    def reap_idle(self, idle_seconds: float | None = None) -> int:
        """Close idle connections unused longer than the idle limit; returns the count."""
        limit = idle_seconds
        if limit is None:
            limit = self._observed_idle
        with self._cond:
            expired = self._pop_expired(limit)
            if expired:
                self._cond.notify_all()
        for item in expired:
            self._close(item.connection)
        return len(expired)

    def close_all(self) -> None:
        """Close every idle connection; borrowed ones are closed when returned."""
        with self._cond:
            self._epoch += 1
            idle = list(self._idle)
            self._idle.clear()
            self._reaper_stop.set()
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

    def _pop_expired(self, idle_seconds: float) -> list[_Lease]:
        """Remove expired idle leases; the caller holds the lock and closes them."""
        now = self._clock()
        expired = [item for item in self._idle if now - item.last_used > idle_seconds]
        if expired:
            ids = {id(item) for item in expired}
            self._idle = deque(item for item in self._idle if id(item) not in ids)
        return expired

    def _start_reaper(self) -> None:
        interval = self._reaper_interval
        if interval is None:
            return
        with self._cond:
            if (
                self._reaper is not None
                and self._reaper.is_alive()
                and not self._reaper_stop.is_set()
            ):
                return
            self._reaper_stop = stop = threading.Event()
            self._reaper = threading.Thread(
                target=self._reap_loop,
                args=(stop, interval),
                name="read-pool-reaper",
                daemon=True,
            )
            self._reaper.start()

    def _reap_loop(self, stop: threading.Event, interval: float) -> None:
        while not stop.wait(interval):
            with suppress(Exception):
                self.reap_idle()

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


def _has_context(exc: BaseException) -> bool:
    return exc.__cause__ is not None or exc.__context__ is not None


def discard_reason(exc: BaseException, connection: SnowflakeConnection | None = None) -> str | None:
    """Classify an error that escaped a pooled connection's use.

    Returns ``None`` when the connection may safely be reused (check it in), otherwise a
    low-cardinality reason string for discarding it (suitable as a metric label). Keep is
    deliberately narrow and only applies when the session is demonstrably idle and open:

    - ``PublicQueryError`` (our own validation) and ``AtlasDataUnavailableError`` raised
      by our own code with no ``__cause__``/``__context__`` (the type also wraps real
      connection failures, so any chained error means discard).
    - connector ``ProgrammingError``/``DataError`` (a SQL or data error the server
      reported for a finished statement), unless its errno or message indicates a timeout,
      cancellation or lost session.

    Everything else discards: timeouts, ``CancelledError``, ``KeyboardInterrupt`` and other
    ``BaseException``s, network/auth/``OperationalError``/``DatabaseError`` and other
    connector errors, a closed connection, and unknown types. When unsure, discard, so a
    possibly mid-statement session is never reused.
    """
    if connection is not None and ReadSessionPool._is_closed(connection):
        return "connection_closed"
    if not isinstance(exc, Exception):
        return "cancelled_or_interrupted"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, PublicQueryError):
        return None if not _has_context(exc) else "wrapped_error"
    if isinstance(exc, AtlasDataUnavailableError):
        return None if not _has_context(exc) else "wrapped_error"
    if isinstance(exc, (sf_errors.ProgrammingError, sf_errors.DataError)):
        errno = getattr(exc, "errno", None)
        text = str(exc).lower()
        if errno in _SESSION_LOSS_ERRNOS or any(p in text for p in _SESSION_LOSS_PHRASES):
            return "session_or_statement_lost"
        return None
    if isinstance(exc, sf_errors.Error):
        return "connector_error"
    return "unknown_error"


_POOL = ReadSessionPool()


def get_read_session_pool() -> ReadSessionPool:
    return _POOL


@contextmanager
def read_connection(settings: ApiSettings) -> Iterator[SnowflakeConnection]:
    """Lend a pooled read connection to this request.

    Returned on success or a benign error; closed otherwise (see ``discard_reason``).
    """
    pool = get_read_session_pool()
    connection = pool.checkout(settings)
    try:
        yield connection
    except BaseException as exc:
        if discard_reason(exc, connection) is None:
            pool.checkin()
        else:
            pool.drop_current()
        raise
    pool.checkin()
