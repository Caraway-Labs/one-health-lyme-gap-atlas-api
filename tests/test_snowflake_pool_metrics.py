"""Pool telemetry: bounded labels, no sensitive data, never raises into the request (API #326)."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from snowflake.connector import errors as sf_errors

from lyme_gap_atlas_api import snowflake_pool_metrics as metrics_module
from lyme_gap_atlas_api import snowflake_read_session as read_module
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError
from lyme_gap_atlas_api.snowflake_pool_metrics import (
    CHECKOUT_OUTCOMES,
    DISCARD_REASONS,
    READ_METRICS,
    WRITE_METRICS,
    PoolMetrics,
    pool_stats,
)
from lyme_gap_atlas_api.snowflake_write_session import WritePoolExhaustedError, WriteSessionPool

ALLOWED_ATTRIBUTES = {
    "atlas.pool.name",
    "atlas.pool.outcome",
    "atlas.pool.wait_ms",
    "atlas.pool.in_use",
    "atlas.pool.idle",
    "atlas.pool.max_size",
    "atlas.pool.discard_reason",
    "atlas.pool.evicted",
}
SECRET = "SELECT secret FROM private_table; user=alice token=abc123"


class Conn:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    READ_METRICS.reset()
    WRITE_METRICS.reset()
    monkeypatch.setattr(read_module, "apply_baseline", lambda *a, **k: None)
    monkeypatch.setattr(read_module, "connect", lambda settings: Conn())


@pytest.fixture
def exporter() -> Any:
    provider = TracerProvider()
    exp = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    tracer = provider.get_tracer("pool-test")
    with tracer.start_as_current_span("request"):
        yield exp
    # The span ends when the fixture's context exits; tests read events in-span via
    # the live span attribute below.


def _events(name: str | None = None) -> list[Any]:
    from opentelemetry import trace

    span = trace.get_current_span()
    events = list(getattr(span, "events", []))
    return [e for e in events if name is None or e.name == name]


def _assert_safe(events: list[Any]) -> None:
    assert events
    for event in events:
        assert set(event.attributes) <= ALLOWED_ATTRIBUTES
        for value in event.attributes.values():
            assert SECRET not in str(value)
            assert isinstance(value, (str, int, float))
        assert event.attributes["atlas.pool.name"] in {"read", "write"}


def test_read_checkout_outcomes_and_stats(exporter: Any) -> None:
    pool = read_module.ReadSessionPool(max_size=1, reaper_interval_seconds=None)
    settings = ApiSettings()
    pool.checkout(settings)
    pool.checkin()
    pool.checkout(settings)
    pool.checkin()
    snapshot = READ_METRICS.snapshot()
    assert snapshot["checkouts"]["created"] == 1
    assert snapshot["checkouts"]["reused"] == 1
    assert snapshot["logins"] == 1
    assert snapshot["wait_ms"]["count"] == 2
    events = _events("atlas.snowflake.pool.checkout")
    _assert_safe(events)
    assert [e.attributes["atlas.pool.outcome"] for e in events] == ["created", "reused"]
    assert all(e.attributes["atlas.pool.outcome"] in CHECKOUT_OUTCOMES for e in events)
    assert events[0].attributes["atlas.pool.max_size"] == 1
    assert events[0].attributes["atlas.pool.in_use"] == 1


def test_read_dead_connection_counts_replaced_dead_and_discard(exporter: Any) -> None:
    pool = read_module.ReadSessionPool(max_size=1, reaper_interval_seconds=None)
    settings = ApiSettings()
    first = pool.checkout(settings)
    pool.checkin()
    first.closed = True  # dies while idle
    pool.checkout(settings)
    pool.checkin()
    snapshot = READ_METRICS.snapshot()
    assert snapshot["checkouts"]["replaced_dead"] == 1
    assert snapshot["discards"]["health_check_failed"] == 1
    assert snapshot["logins"] == 2


def test_read_discard_reason_comes_from_classifier(
    monkeypatch: pytest.MonkeyPatch, exporter: Any
) -> None:
    monkeypatch.setattr(read_module, "_POOL", read_module.ReadSessionPool(max_size=2))
    with pytest.raises(TimeoutError), read_module.read_connection(ApiSettings()):
        raise TimeoutError(SECRET)
    with pytest.raises(sf_errors.OperationalError), read_module.read_connection(ApiSettings()):
        raise sf_errors.OperationalError(SECRET)
    # A benign error keeps the connection and records no discard.
    with pytest.raises(AtlasDataUnavailableError), read_module.read_connection(ApiSettings()):
        raise AtlasDataUnavailableError("own")
    assert READ_METRICS.snapshot()["discards"] == {"timeout": 1, "connector_error": 1}
    events = _events("atlas.snowflake.pool.discard")
    _assert_safe(events)
    assert {e.attributes["atlas.pool.discard_reason"] for e in events} <= DISCARD_REASONS


def test_read_exhaustion_idle_eviction_and_log(
    caplog: pytest.LogCaptureFixture, exporter: Any
) -> None:
    pool = read_module.ReadSessionPool(
        max_size=1, checkout_timeout_seconds=0.01, reaper_interval_seconds=None
    )
    settings = ApiSettings()
    pool.checkout(settings)
    with caplog.at_level(logging.INFO), pytest.raises(AtlasDataUnavailableError):
        # Same-thread checkout is re-entrant, so exhaust from a second thread.
        import threading

        errors: list[BaseException] = []

        def other() -> None:
            try:
                pool.checkout(settings)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        raise errors[0]
    assert READ_METRICS.snapshot()["exhausted"] == 1
    assert any(r.getMessage() == "snowflake_pool_exhausted" for r in caplog.records)
    pool.checkin()
    assert pool.reap_idle(-1.0) == 1
    assert READ_METRICS.snapshot()["idle_evictions"] == 1
    _assert_safe(_events("atlas.snowflake.pool.idle_eviction"))


def test_write_pool_outcomes_discards_and_exhaustion(exporter: Any) -> None:
    pool = WriteSessionPool()
    settings = ApiSettings(write_pool_max_size=1, write_pool_checkout_timeout_seconds=0.01)
    with pool.borrow(settings, lambda s: Conn()):
        pass
    with pool.borrow(settings, lambda s: Conn()):
        pass
    with pytest.raises(RuntimeError), pool.borrow(settings, lambda s: Conn()):
        raise RuntimeError(SECRET)
    held = pool.checkout(settings, lambda s: Conn())
    import threading

    errors: list[BaseException] = []

    def other() -> None:
        try:
            pool.checkout(settings, lambda s: Conn())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert isinstance(errors[0], WritePoolExhaustedError)
    pool.release(held)
    snapshot = WRITE_METRICS.snapshot()
    assert snapshot["checkouts"]["created"] == 2
    assert snapshot["checkouts"]["reused"] == 2
    assert snapshot["exhausted"] == 1
    assert snapshot["discards"] == {"borrow_error": 1}
    assert READ_METRICS.snapshot()["wait_ms"]["count"] == 0  # pools are separate
    _assert_safe(_events("atlas.snowflake.pool.checkout"))
    stats = pool_stats()
    assert set(stats) == {"read", "write"}
    assert stats["write"]["logins"] == 2


def test_unknown_labels_collapse_to_bounded_values() -> None:
    metrics = PoolMetrics("write")
    metrics.record_discard(SECRET)
    metrics.record_checkout(SECRET, 0.0)
    snapshot = metrics.snapshot()
    assert snapshot["discards"] == {"other": 1}
    assert set(snapshot["checkouts"]) == CHECKOUT_OUTCOMES
    assert SECRET not in json.dumps(snapshot)
    assert PoolMetrics("not-a-pool").pool == "read"


def test_instrumentation_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("telemetry backend down")

    monkeypatch.setattr(metrics_module.trace, "get_current_span", boom)
    monkeypatch.setattr(metrics_module._logger, "warning", boom)
    monkeypatch.setattr(metrics_module._logger, "info", boom)
    pool = read_module.ReadSessionPool(max_size=1, reaper_interval_seconds=None)
    connection = pool.checkout(ApiSettings())
    assert isinstance(connection, Conn)
    pool.checkin()
    pool.drop_current("timeout")
    READ_METRICS.record_eviction(2)
    READ_METRICS.record_checkout("exhausted", 0.1)
    write = WriteSessionPool()
    with write.borrow(ApiSettings(), lambda s: Conn()):
        pass


def _load_benchmark() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_snowflake_connections.py"
    spec = importlib.util.spec_from_file_location("benchmark_snowflake_connections", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_benchmark_runs_on_fakes_and_pool_beats_no_pool() -> None:
    bench = _load_benchmark()
    output: list[str] = []
    code = bench.main(
        ["--requests", "24", "--concurrency", "6", "--pool-size", "3", "--login-ms", "5"],
        out=output.append,
    )
    assert code == 0
    report = json.loads(output[0])
    assert "not production" in report["evidence"]
    by_key = {(r["scenario"], r["mode"]): r for r in report["results"]}
    for scenario in bench.SCENARIOS:
        base = by_key[(scenario.name, "no_pool")]
        pooled = by_key[(scenario.name, "pooled")]
        assert base["logins_per_call"] == scenario.borrows * (1 + scenario.nested)
        assert pooled["logins"] <= 3
        assert pooled["logins_per_call"] < base["logins_per_call"]
        assert pooled["failed_requests"] == 0
    assert "|" in bench.markdown_table(report["results"])


@pytest.mark.parametrize(
    ("database", "role", "env", "fragment"),
    [
        ("ONE_HEALTH_PROD", "R", {"ATLAS_BENCHMARK_ALLOW_STAGING": "1"}, "production"),
        ("OH_DEV", "ACCOUNTADMIN", {"ATLAS_BENCHMARK_ALLOW_STAGING": "1"}, "production"),
        ("OH_DEV", "R", {}, "ATLAS_BENCHMARK_ALLOW_STAGING"),
        ("OH_DEV", "R", {"ATLAS_BENCHMARK_ALLOW_STAGING": "1"}, "exact configured database"),
    ],
)
def test_staging_run_refuses_without_flags_or_for_prod(
    database: str, role: str, env: dict[str, str], fragment: str
) -> None:
    bench = _load_benchmark()
    output: list[str] = []
    settings = ApiSettings(snowflake_database=database, snowflake_role=role)
    code = bench.main(["--staging"], env=env, settings=settings, out=output.append)
    assert code == 2 and fragment in output[0]


def test_staging_guard_accepts_explicit_non_prod_confirmation() -> None:
    bench = _load_benchmark()
    settings = ApiSettings(snowflake_database="OH_LYME_DEV", snowflake_role="OH_LYME_DEV_READ")
    env = {"ATLAS_BENCHMARK_ALLOW_STAGING": "1", "ATLAS_BENCHMARK_CONFIRM_DATABASE": "OH_LYME_DEV"}
    assert bench.check_staging_guard(env, settings) is None
