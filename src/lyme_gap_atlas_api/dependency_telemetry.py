"""Measure actual I/O calls, never query content or connection lifetime."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from time import perf_counter
from typing import Any, cast

import httpx
from lyme_gap_atlas_shared.settings import SnowflakeSettings
from lyme_gap_atlas_shared.snowflake import connect as shared_connect
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from snowflake.connector import SnowflakeConnection

from .cdc_timing import CURRENT as CDC_CURRENT
from .climate_timing import CURRENT
from .context_timing import CURRENT as CONTEXT_CURRENT
from .tier1_timing import CURRENT as TIER1_CURRENT

DEPENDENCIES = frozenset({"snowflake", "supabase"})
OPERATIONS = frozenset(
    {
        "connect",
        "connection_enter",
        "connection_teardown",
        "cursor",
        "cursor_enter",
        "cursor_teardown",
        "execute",
        "fetchone",
        "fetchmany",
        "fetchall",
        "close",
        "jwks_fetch",
        "profile_request",
        "privacy_request",
        "auth_admin_request",
    }
)


def failure_class(error: BaseException) -> str:
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(error, httpx.HTTPStatusError):
        return "upstream_http"
    return "dependency_error"


@contextmanager
def dependency_span(system: str, operation: str) -> Iterator[None]:
    system = system if system in DEPENDENCIES else "other"
    operation = operation if operation in OPERATIONS else "other"
    started = perf_counter()
    span = None
    with suppress(Exception):
        span = trace.get_tracer(__name__).start_span(
            f"atlas.dependency.{system}.{operation}",
            kind=trace.SpanKind.CLIENT,
            attributes={"atlas.dependency.system": system, "atlas.dependency.operation": operation},
            record_exception=False,
            set_status_on_exception=False,
        )
    try:
        manager = (
            trace.use_span(
                span, end_on_exit=False, record_exception=False, set_status_on_exception=False
            )
            if span is not None
            else nullcontext()
        )
        with manager:
            try:
                yield
            except BaseException as error:
                with suppress(Exception):
                    if span is not None:
                        span.set_attribute("atlas.dependency.outcome", "failure")
                        span.set_attribute("atlas.dependency.failure_class", failure_class(error))
                        span.set_status(StatusCode.ERROR)
                raise
            else:
                with suppress(Exception):
                    if span is not None:
                        span.set_attribute("atlas.dependency.outcome", "success")
                        span.set_attribute("atlas.dependency.failure_class", "none")
    finally:
        with suppress(Exception):
            timing = CURRENT.get()
            if timing is not None and system == "snowflake":
                timing.record(operation, perf_counter() - started)
            tier1_timing = TIER1_CURRENT.get()
            if tier1_timing is not None and system == "snowflake":
                tier1_timing.record(operation, perf_counter() - started)
        with suppress(Exception):
            cdc_timing = CDC_CURRENT.get()
            if cdc_timing is not None and system == "snowflake":
                cdc_timing.record(operation, perf_counter() - started)
        with suppress(Exception):
            context_timing = CONTEXT_CURRENT.get()
            if context_timing is not None and system == "snowflake":
                context_timing.record(operation, perf_counter() - started)
        with suppress(Exception):
            if span is not None:
                span.end()


class _SnowflakeBoundary:
    """Delegate SDK lifecycle exactly, instrument only known external operations."""

    def __init__(self, resource: Any, kind: str) -> None:
        self._resource = resource
        self._kind = kind

    def __getattr__(self, name: str) -> Any:
        original = getattr(self._resource, name)
        if name not in {"cursor", "execute", "fetchone", "fetchmany", "fetchall", "close"}:
            return original

        def call(*args: Any, **kwargs: Any) -> Any:
            if name == "cursor":
                return _SnowflakeBoundary(original(*args, **kwargs), "cursor")
            with dependency_span("snowflake", name):
                result = original(*args, **kwargs)
            return self if result is self._resource else result

        return call

    def __enter__(self) -> "_SnowflakeBoundary":
        entered = self._resource.__enter__()
        return self if entered is self._resource else _SnowflakeBoundary(entered, self._kind)

    def __exit__(self, *args: Any) -> Any:
        with dependency_span("snowflake", f"{self._kind}_teardown"):
            return self._resource.__exit__(*args)


def connect(settings: SnowflakeSettings, *, include_database: bool = True) -> SnowflakeConnection:
    """Reuse approved shared connection construction; do not duplicate credentials."""
    with dependency_span("snowflake", "connect"):
        connection = shared_connect(settings, include_database=include_database)
    return cast(SnowflakeConnection, _SnowflakeBoundary(connection, "connection"))
