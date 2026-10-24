"""Process-scoped, thread-local Snowflake read connections for public read paths."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from snowflake.connector import SnowflakeConnection

from .dependency_telemetry import connect

if TYPE_CHECKING:
    from .config import ApiSettings

DEFAULT_IDLE_SECONDS = 300.0


class _ThreadReadSession:
    def __init__(self, connection: SnowflakeConnection) -> None:
        self.connection = connection
        self.last_used = time.monotonic()


class ReadSessionPool:
    """One reusable read connection per worker thread; not for write paths."""

    def __init__(self, *, idle_seconds: float = DEFAULT_IDLE_SECONDS) -> None:
        self._idle_seconds = idle_seconds
        self._lock = threading.Lock()
        self._sessions: dict[int, _ThreadReadSession] = {}

    def checkout(self, settings: ApiSettings) -> SnowflakeConnection:
        thread_id = threading.get_ident()
        now = time.monotonic()
        with self._lock:
            session = self._sessions.get(thread_id)
            if session is not None and not session.connection.is_closed():
                if now - session.last_used <= self._idle_seconds:
                    session.last_used = now
                    return session.connection
                self._drop(thread_id, session)

        connection = connect(settings)
        with self._lock:
            session = _ThreadReadSession(connection)
            self._sessions[thread_id] = session
            session.last_used = now
            return session.connection

    def drop_current(self) -> None:
        thread_id = threading.get_ident()
        with self._lock:
            session = self._sessions.pop(thread_id, None)
        if session is not None:
            self._close(session.connection)

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._close(session.connection)

    def _drop(self, thread_id: int, session: _ThreadReadSession) -> None:
        self._sessions.pop(thread_id, None)
        self._close(session.connection)

    @staticmethod
    def _close(connection: SnowflakeConnection) -> None:
        try:
            if not connection.is_closed():
                connection.close()
        except Exception:
            pass


_POOL = ReadSessionPool()


def get_read_session_pool() -> ReadSessionPool:
    return _POOL


@contextmanager
def read_connection(settings: ApiSettings) -> Iterator[SnowflakeConnection]:
    """Yield the thread's pooled read connection. Do not close it on success."""
    pool = get_read_session_pool()
    connection = pool.checkout(settings)
    try:
        yield connection
    except BaseException:
        pool.drop_current()
        raise
