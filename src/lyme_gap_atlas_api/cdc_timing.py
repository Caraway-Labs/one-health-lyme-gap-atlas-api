"""Bounded request-local CDC timings, independent of trace sampling."""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter

from opentelemetry import trace

PHASES = frozenset(
    {
        "service",
        "session_setup",
        "release_lookup",
        "identity",
        "authority_query",
        "producer",
        "producer_processing",
        "mapping",
    }
)
IO_OPERATIONS = frozenset(
    {
        "connect",
        "execute",
        "fetchone",
        "fetchmany",
        "fetchall",
        "cursor_teardown",
        "connection_teardown",
        "close",
    }
)


@dataclass
class CDCTiming:
    durations: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    serving_path: str | None = None
    processed_counties: int | None = None
    batch_envelope_bytes: int | None = None
    started: float | None = None
    finished: float | None = None

    def record(self, name: str, seconds: float) -> None:
        if name in PHASES | IO_OPERATIONS:
            self.durations[name] = self.durations.get(name, 0.0) + max(0.0, seconds) * 1000
            self.counts[name] = self.counts.get(name, 0) + 1


CURRENT: ContextVar[CDCTiming | None] = ContextVar("cdc_timing", default=None)


@contextmanager
def cdc_request_timing(timing: CDCTiming) -> Iterator[None]:
    token = CURRENT.set(timing)
    timing.started = perf_counter()
    try:
        with phase("service"):
            yield
    finally:
        timing.finished = perf_counter()
        CURRENT.reset(token)


@contextmanager
def phase(name: str) -> Iterator[None]:
    timing = CURRENT.get()
    if timing is None or name not in PHASES:
        yield
        return
    started = perf_counter()
    span = None
    with suppress(Exception):
        span = trace.get_tracer(__name__).start_span(
            f"atlas.cdc.{name}", record_exception=False, set_status_on_exception=False
        )
    manager = (
        trace.use_span(
            span, end_on_exit=False, record_exception=False, set_status_on_exception=False
        )
        if span is not None
        else nullcontext()
    )
    try:
        with manager:
            yield
    finally:
        with suppress(Exception):
            timing.record(name, perf_counter() - started)
        with suppress(Exception):
            if span is not None:
                span.end()


def record_processing(seconds: float) -> None:
    """Exclusive producer wall time outside its serial query callbacks, not CPU time."""
    with suppress(Exception):
        timing = CURRENT.get()
        if timing is not None:
            timing.record("producer_processing", seconds)


def record_selection(path: str, count: int) -> None:
    """Closed serving mode and bounded count, never county identities."""
    with suppress(Exception):
        timing = CURRENT.get()
        if timing is not None and path in {"batch", "singleton"} and 1 <= count <= 500:
            timing.serving_path = path
            timing.processed_counties = count


def record_envelope(value: object) -> None:
    """Measure already encoded JSON only; native VARIANT size stays unavailable."""
    with suppress(Exception):
        timing = CURRENT.get()
        if timing is not None and isinstance(value, str):
            timing.batch_envelope_bytes = min(len(value.encode("utf-8")), 8 * 1024 * 1024 + 1)
