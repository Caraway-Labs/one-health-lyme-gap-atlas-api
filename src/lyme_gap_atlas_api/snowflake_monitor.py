"""Process-local readiness latch and a single owner for dependency monitoring."""

import math
import time
from collections.abc import Callable
from contextlib import suppress
from threading import Event, Thread
from typing import Any, Literal

from pydantic import BaseModel

from .config import ApiSettings
from .dependency_telemetry import connect, dependency_span
from .telemetry_logging import operational_logger

logger = operational_logger(__name__)


class ReadinessDependencies(BaseModel):
    snowflake: Literal["ok", "degraded"]


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"
    dependencies: ReadinessDependencies


class SnowflakeMonitor:
    """Only the worker touches the connection; HTTP reads the published state.

    Connector timeouts bound individual blocking calls. The monotonic deadline
    bounds retries and statement time across a check, including reconnect. No
    new workers are spawned on timeout, so a stalled SDK cannot accumulate work.
    """

    def __init__(self, settings: ApiSettings, probe: Callable[[], bool] | None = None) -> None:
        self.settings = settings
        self._probe = probe
        self._connection: Any = None
        self._stop = Event()
        self.initial_check = Event()
        self._thread: Thread | None = None
        self.startup_succeeded = False
        self.dependency_state: Literal["ok", "degraded"] = "degraded"
        self._previous: bool | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self.initial_check.clear()
        self.startup_succeeded = False
        self._previous = None
        self._thread = Thread(target=self._run, name="snowflake-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # Connection cleanup happens on its owning thread, never concurrently
            # with execute. A late SDK return still runs the finally cleanup.
            self._thread.join(timeout=self.settings.snowflake_startup_check_timeout_seconds + 3)
            if not self._thread.is_alive():
                self._thread = None

    def _close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            with suppress(Exception):
                connection.close(retry=False)

    def check(self, budget: float = 3) -> bool:
        deadline = time.monotonic() + budget
        healthy = False
        try:
            with dependency_span("snowflake", "check"):
                if self._probe is not None:
                    healthy = self._probe()
                else:
                    # An existing broken session gets one reconnect attempt.
                    attempts = 2 if self._connection is not None else 1
                    for attempt in range(attempts):
                        try:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0 or self._stop.is_set():
                                raise TimeoutError
                            if self._connection is None:
                                # SDK timeout properties truncate to integer seconds;
                                # never start a login whose timeout would become zero.
                                if remaining < 1:
                                    raise TimeoutError
                                self._connection = connect(self.settings, timeout_seconds=remaining)
                            remaining = deadline - time.monotonic()
                            if remaining <= 0 or self._stop.is_set():
                                raise TimeoutError
                            with self._connection.cursor() as cursor:
                                cursor.execute("SELECT 1", timeout=max(1, math.ceil(remaining)))
                                if cursor.fetchone() != (1,):
                                    raise RuntimeError("Unexpected connectivity result")
                            if time.monotonic() > deadline:
                                raise TimeoutError
                            healthy = True
                            break
                        except Exception:
                            self._close()
                            if attempt + 1 == attempts:
                                raise
                if time.monotonic() > deadline:
                    self._close()
                    raise TimeoutError
                if not healthy:
                    raise RuntimeError("Connectivity check failed")
        except Exception:
            healthy = False
        self.dependency_state = "ok" if healthy else "degraded"
        if healthy:
            self.startup_succeeded = True
        if healthy != self._previous:
            if not healthy:
                logger.warning("snowflake_dependency_degraded")
            elif self._previous is False:
                logger.info("snowflake_dependency_recovered")
        self._previous = healthy
        return healthy

    def _run(self) -> None:
        try:
            self.check(self.settings.snowflake_startup_check_timeout_seconds)
            self.initial_check.set()
            while not self._stop.wait(self.settings.snowflake_monitor_interval_seconds):
                self.check()
        finally:
            self.initial_check.set()
            self._close()
