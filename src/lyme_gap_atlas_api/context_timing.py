"""Request-local county-context observation timings; fixed dimensions, no query content."""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter

from opentelemetry import trace

PHASES = frozenset({"release_lookup", "measure_lookup", "observation_query", "mapping", "service"})
IO_OPERATIONS = frozenset(
    {"connect", "execute", "fetchone", "fetchall", "cursor_teardown", "connection_teardown"}
)


@dataclass
class ContextTiming:
    durations: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    started: float | None = None
    finished: float | None = None

    def record(self, name: str, seconds: float) -> None:
        if name not in PHASES and name not in IO_OPERATIONS:
            return
        self.durations[name] = self.durations.get(name, 0.0) + max(0.0, seconds) * 1000
        self.counts[name] = self.counts.get(name, 0) + 1


CURRENT: ContextVar[ContextTiming | None] = ContextVar("context_timing", default=None)


@contextmanager
def context_request_timing(timing: ContextTiming) -> Iterator[None]:
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
            f"atlas.context.{name}", record_exception=False, set_status_on_exception=False
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
            if span is not None:
                span.end()
