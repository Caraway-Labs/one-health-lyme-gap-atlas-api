"""Controlled dependency failures and clock progression; no remote fault injection."""

import logging
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api import dependency_telemetry
from lyme_gap_atlas_api import snowflake_monitor as module
from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError


class Connection:
    def __init__(self):
        self.executions = []
        self.closed = 0
        self.failure = False

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, *, timeout):
        self.executions.append((sql, timeout))
        if self.failure:
            raise RuntimeError("private credentials, SQL, and host")

    def fetchone(self):
        return (1,)

    def close(self, *, retry):
        assert retry is False
        self.closed += 1


@pytest.fixture
def connection(monkeypatch):
    value = Connection()
    connect = Mock(return_value=value)
    monkeypatch.setattr(module, "connect", connect)
    return value, connect


def test_reuse_reconnect_and_state_change_logs(connection, caplog):
    first, connect = connection
    monitor = module.SnowflakeMonitor(ApiSettings())
    caplog.set_level(logging.INFO)
    assert not monitor.startup_succeeded
    assert monitor.check(10)
    assert monitor.check()
    assert connect.call_count == 1
    assert len(first.executions) == 2
    assert monitor.startup_succeeded
    second = Connection()
    first.failure = True
    connect.return_value = second
    assert monitor.check()
    assert connect.call_count == 2
    assert first.closed == 1
    second.failure = True
    connect.side_effect = RuntimeError("private credentials")
    assert not monitor.check()
    assert not monitor.check()
    assert monitor.dependency_state == "degraded"
    assert monitor.startup_succeeded
    third = Connection()
    connect.side_effect = None
    connect.return_value = third
    assert monitor.check()
    assert monitor.check()
    assert [r.msg for r in caplog.records] == [
        "snowflake_dependency_degraded",
        "snowflake_dependency_recovered",
    ]
    assert "private credentials" not in caplog.text
    monitor._close()
    assert third.closed == 1


def test_deadline_covers_connect_and_query_and_no_unbounded_retry(connection, monkeypatch):
    value, connect = connection
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monitor = module.SnowflakeMonitor(ApiSettings())

    def slow_connect(*args, **kwargs):
        clock[0] += 4
        return value

    connect.side_effect = slow_connect
    assert not monitor.check(3)
    assert not monitor.startup_succeeded
    assert not value.executions
    assert value.closed == 1
    assert connect.call_count == 1


def test_reconnect_respects_remaining_check_budget(connection, monkeypatch):
    first, connect = connection
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monitor = module.SnowflakeMonitor(ApiSettings())
    assert monitor.check()

    def fail(sql, *, timeout):
        clock[0] += 2.5
        raise TimeoutError

    first.execute = fail
    assert not monitor.check()
    assert connect.call_count == 1  # Subsecond budget must not become an SDK zero timeout.
    assert monitor.startup_succeeded


def test_thread_cadence_and_shutdown_close(connection):
    value, connect = connection
    monitor = module.SnowflakeMonitor(ApiSettings())
    monitor.start()
    assert monitor.initial_check.wait(2)
    assert monitor.startup_succeeded
    assert connect.call_count == 1
    monitor.start()  # Idempotent start never duplicates workers.
    monitor.stop()
    monitor.stop()
    assert value.closed == 1
    assert monitor._thread is None


def test_worker_wait_uses_configured_interval_and_closes(connection):
    value, connect = connection
    monitor = module.SnowflakeMonitor(ApiSettings(snowflake_monitor_interval_seconds=42))
    waits = []

    class Clock:
        def is_set(self):
            return False

        def wait(self, interval):
            waits.append(interval)
            return len(waits) == 3

    monitor._stop = Clock()
    monitor._run()
    assert waits == [42, 42, 42]
    assert connect.call_count == 1
    assert len(value.executions) == 3
    assert value.closed == 1


def test_readiness_is_io_free_and_outage_keeps_existing_data_503(connection, monkeypatch):
    value, connect = connection
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)

    class Repository:
        def ready(self):
            return True

        def load_snapshot(self):
            raise AtlasDataUnavailableError("private dependency error")

    app = create_app(Repository(), ApiSettings())
    monitor = app.state.snowflake_monitor
    # Use the production monitor with controlled connect instead of the fixture probe.
    monitor._probe = None
    with TestClient(app) as api:
        assert monitor.startup_succeeded
        calls = connect.call_count, len(value.executions)
        for _ in range(5):
            assert api.get("/health/ready").json() == {
                "status": "ready",
                "dependencies": {"snowflake": "ok"},
            }
        assert calls == (connect.call_count, len(value.executions))
        value.failure = True
        connect.side_effect = RuntimeError("broken credentials")
        assert not monitor.check()
        calls = connect.call_count, len(value.executions)
        response = api.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["dependencies"] == {"snowflake": "degraded"}
        assert calls == (connect.call_count, len(value.executions))
        assert api.get("/health/live").json() == {"status": "ok"}
        failed = api.get("/v1/atlas/metadata")
        assert failed.status_code == 503
        assert failed.json()["detail"] == (
            "The governed Atlas data service is temporarily unavailable."
        )


def test_broken_credentials_never_ready_then_success_latches(connection, monkeypatch):
    value, connect = connection
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    monkeypatch.setattr("lyme_gap_atlas_api.app.start_reader_probe", lambda *a: None)
    monkeypatch.setattr("lyme_gap_atlas_api.app.start_county_context_probe", lambda *a: None)
    monkeypatch.setattr("lyme_gap_atlas_api.app.start_model_serving_probe", lambda *a: None)
    app = create_app(settings=ApiSettings())
    monitor = app.state.snowflake_monitor
    assert not monitor.startup_succeeded
    connect.side_effect = RuntimeError("broken credentials")
    with TestClient(app) as api:
        assert api.get("/health/ready").status_code == 503
        assert api.get("/health/live").status_code == 200
        assert not monitor.check()
        assert api.get("/health/ready").status_code == 503
        connect.side_effect = None
        assert monitor.check()
        assert api.get("/health/ready").status_code == 200
    assert value.closed == 1


def test_monitor_connection_timeouts_do_not_change_product_connection_policy(monkeypatch):
    parameters = {"account": "fake", "session_parameters": {"QUERY_TAG": "safe"}}
    monkeypatch.setattr(
        dependency_telemetry, "connection_parameters", lambda *a, **k: parameters.copy()
    )
    bounded = Mock(return_value=Connection())
    ordinary = Mock(return_value=Connection())
    monkeypatch.setattr(dependency_telemetry.snowflake.connector, "connect", bounded)
    monkeypatch.setattr(dependency_telemetry, "shared_connect", ordinary)
    dependency_telemetry.connect(ApiSettings(), timeout_seconds=2.5)
    kwargs = bounded.call_args.kwargs
    assert kwargs["login_timeout"] == 2.5
    assert kwargs["network_timeout"] == 2.5
    assert kwargs["socket_timeout"] == 2.5
    assert kwargs["client_session_keep_alive"] is False
    assert kwargs["client_telemetry_enabled"] is False
    assert not ordinary.called
    dependency_telemetry.connect(ApiSettings())
    ordinary.assert_called_once()


@pytest.mark.parametrize(
    "field", ["snowflake_monitor_interval_seconds", "snowflake_startup_check_timeout_seconds"]
)
def test_configuration_rejects_zero_budget(field):
    with pytest.raises(ValueError):
        ApiSettings(**{field: 0})


def test_before_lifespan_readiness_is_unready_without_io(connection, monkeypatch):
    value, connect = connection
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    api = TestClient(create_app(settings=ApiSettings()))
    assert api.get("/health/ready").status_code == 503
    assert api.get("/health/live").json() == {"status": "ok"}
    assert not connect.called
    assert not value.executions


def test_check_spans_are_background_roots_and_readiness_has_no_dependency_children(
    connection, monkeypatch
):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)

    class Repository:
        def ready(self):
            return True

    app = create_app(Repository(), ApiSettings())
    app.state.snowflake_monitor._probe = None
    with (
        TestClient(app) as api,
        trace.get_tracer(__name__).start_as_current_span("readiness-test"),
    ):
        assert api.get("/health/ready").status_code == 200
    checks = [
        s for s in exporter.get_finished_spans() if s.name == "atlas.dependency.snowflake.check"
    ]
    assert len(checks) == 1
    assert checks[0].parent is None
    ready_spans = [s for s in exporter.get_finished_spans() if s.name == "GET /health/ready"]
    assert len(ready_spans) == 1
    request_trace = ready_spans[0].context.trace_id
    assert not [
        s
        for s in exporter.get_finished_spans()
        if s.name.startswith("atlas.dependency.snowflake.") and s.context.trace_id == request_trace
    ]
