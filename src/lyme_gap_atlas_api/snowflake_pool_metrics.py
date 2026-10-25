"""Low-cardinality Snowflake pool telemetry shared by the read and write pools.

There is no OpenTelemetry metrics pipeline in this API, so none is introduced here. Each
pool event is (a) counted in a process-local, thread-safe ``PoolMetrics`` exposed through
``pool_stats()``, (b) added as a fixed-name event on the current span (a request or monitor
span), and (c) for the rare discard/eviction/exhaustion cases, written as a fixed-name
structured log. Login duration is not repeated: ``atlas.dependency.snowflake.connect``
already measures it. Labels are closed sets (pool, outcome, reason); never put SQL, query
text, user names, tokens, user data or per-request IDs here. Nothing here may raise into
the request path.
"""

from __future__ import annotations

import threading
from contextlib import suppress
from typing import Any

from opentelemetry import trace

from .telemetry_logging import operational_logger

POOLS = frozenset({"read", "write"})
CHECKOUT_OUTCOMES = frozenset({"reused", "created", "replaced_dead", "exhausted"})
# Read pool reasons are ``discard_reason`` values (#339) plus pool-side ones; write pool
# reasons are the fixed pool-side ones. Anything else collapses to ``other``.
DISCARD_REASONS = frozenset(
    {
        # ``snowflake_read_session.discard_reason`` classifications
        "connection_closed",
        "cancelled_or_interrupted",
        "timeout",
        "wrapped_error",
        "session_or_statement_lost",
        "connector_error",
        "unknown_error",
        # pool-side reasons
        "health_check_failed",
        "dirty_lease",
        "stale_epoch",
        "baseline_failed",
        "borrow_error",
        "settings_changed",
        "unspecified",
        "other",
    }
)
# Upper bounds in milliseconds; the last bucket is open-ended (``+Inf``).
WAIT_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)

_logger = operational_logger(__name__)


def _bucket(wait_ms: float) -> str:
    for bound in WAIT_BUCKETS_MS:
        if wait_ms <= bound:
            return str(bound)
    return "+Inf"


class PoolMetrics:
    """Counters for one pool; label values come only from the closed sets above."""

    def __init__(self, pool: str) -> None:
        self.pool = pool if pool in POOLS else "read"
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._checkouts = dict.fromkeys(sorted(CHECKOUT_OUTCOMES), 0)
            self._discards: dict[str, int] = {}
            self._evictions = 0
            self._wait_buckets = dict.fromkeys([*map(str, WAIT_BUCKETS_MS), "+Inf"], 0)
            self._wait_count = 0
            self._wait_sum_ms = 0.0
            self._wait_max_ms = 0.0
            self._max_size: int | None = None

    def record_checkout(
        self,
        outcome: str,
        wait_seconds: float,
        *,
        in_use: int | None = None,
        idle: int | None = None,
        max_size: int | None = None,
    ) -> None:
        with suppress(Exception):
            outcome = outcome if outcome in CHECKOUT_OUTCOMES else "created"
            wait_ms = max(0.0, float(wait_seconds)) * 1000
            with self._lock:
                self._checkouts[outcome] += 1
                self._wait_buckets[_bucket(wait_ms)] += 1
                self._wait_count += 1
                self._wait_sum_ms += wait_ms
                self._wait_max_ms = max(self._wait_max_ms, wait_ms)
                if max_size is not None:
                    self._max_size = int(max_size)
            attributes: dict[str, str | int | float] = {
                "atlas.pool.name": self.pool,
                "atlas.pool.outcome": outcome,
                "atlas.pool.wait_ms": round(wait_ms, 3),
            }
            if in_use is not None:
                attributes["atlas.pool.in_use"] = int(in_use)
            if idle is not None:
                attributes["atlas.pool.idle"] = int(idle)
            if max_size is not None:
                attributes["atlas.pool.max_size"] = int(max_size)
            trace.get_current_span().add_event("atlas.snowflake.pool.checkout", attributes)
            if outcome == "exhausted":
                _logger.warning(
                    "snowflake_pool_exhausted",
                    extra={"pool": self.pool, "wait_ms": round(wait_ms, 3)},
                )

    def record_discard(self, reason: str | None) -> None:
        with suppress(Exception):
            label = reason if reason in DISCARD_REASONS else "other"
            with self._lock:
                self._discards[label] = self._discards.get(label, 0) + 1
            trace.get_current_span().add_event(
                "atlas.snowflake.pool.discard",
                {"atlas.pool.name": self.pool, "atlas.pool.discard_reason": label},
            )
            _logger.info("snowflake_pool_discard", extra={"pool": self.pool, "reason": label})

    def record_eviction(self, count: int = 1) -> None:
        with suppress(Exception):
            if count <= 0:
                return
            with self._lock:
                self._evictions += count
            trace.get_current_span().add_event(
                "atlas.snowflake.pool.idle_eviction",
                {"atlas.pool.name": self.pool, "atlas.pool.evicted": int(count)},
            )
            _logger.info(
                "snowflake_pool_idle_eviction", extra={"pool": self.pool, "count": int(count)}
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            checkouts = dict(self._checkouts)
            return {
                "pool": self.pool,
                "checkouts": checkouts,
                "logins": checkouts["created"] + checkouts["replaced_dead"],
                "exhausted": checkouts["exhausted"],
                "discards": dict(self._discards),
                "idle_evictions": self._evictions,
                "wait_ms": {
                    "count": self._wait_count,
                    "sum": round(self._wait_sum_ms, 3),
                    "max": round(self._wait_max_ms, 3),
                    "buckets": dict(self._wait_buckets),
                },
                "max_size": self._max_size,
            }


READ_METRICS = PoolMetrics("read")
WRITE_METRICS = PoolMetrics("write")


def pool_stats() -> dict[str, dict[str, Any]]:
    """In-process snapshot of both pools: counters plus live idle/in_use/creating sizes.

    Diagnostic only; never served by an endpoint and never carries connection identity.
    """
    from .snowflake_read_session import get_read_session_pool
    from .snowflake_write_session import get_write_session_pool

    return {
        "read": {**READ_METRICS.snapshot(), "size": get_read_session_pool().stats()},
        "write": {**WRITE_METRICS.snapshot(), "size": get_write_session_pool().stats()},
    }
