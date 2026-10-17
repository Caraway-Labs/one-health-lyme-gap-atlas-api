"""Bounded monotonic telemetry with real threadpool propagation and no data payload."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_environmental_context import FixtureRepository, params

from lyme_gap_atlas_api import climate_timing as timing
from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.dependency_telemetry import dependency_span
from lyme_gap_atlas_api.telemetry_logging import completion_context


def test_phase_dependency_clock_failure_and_privacy(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    clock = [0.0]
    monkeypatch.setattr(timing, "perf_counter", lambda: clock[0])
    monkeypatch.setattr("lyme_gap_atlas_api.dependency_telemetry.perf_counter", lambda: clock[0])
    recorder = timing.ClimateTiming()
    with (
        trace.get_tracer("test").start_as_current_span("request"),
        pytest.raises(ValueError),
        timing.climate_request_timing(recorder),
        timing.phase("observation_query"),
        dependency_span("snowflake", "execute"),
    ):
        clock[0] = 0.125
        raise ValueError("private SQL credentials")
    assert timing.CURRENT.get() is None
    assert recorder.durations == {"execute": 125, "observation_query": 125, "service": 125}
    assert recorder.counts == {"execute": 1, "observation_query": 1, "service": 1}
    assert recorder.started == 0 and recorder.finished == 0.125
    spans = exporter.get_finished_spans()
    by_name = {s.name: s for s in spans}
    execute = by_name["atlas.dependency.snowflake.execute"]
    observation = by_name["atlas.climate.observation_query"]
    service = by_name["atlas.climate.service"]
    assert execute.parent.span_id == observation.context.span_id
    assert observation.parent.span_id == service.context.span_id
    assert service.parent.span_id == by_name["request"].context.span_id
    assert not any(s.events or s.status.description for s in spans)
    assert "private" not in json.dumps([dict(s.attributes) for s in spans])


def test_closed_schema_drops_injected_dimensions_and_nonfinite_values():
    result = completion_context(
        "api_request_completed",
        {
            "climate_timing_ms": {"connect": 12.5, "execute": float("inf"), "privateSQL": 2},
            "climate_operation_counts": {"connect": 1, "fetchall": -1, "privateID": "secret"},
            "climate_before_service_ms": 1,
            "climate_after_service_ms": float("nan"),
            "query_id": "secret",
        },
    )
    assert result["climate_timing_ms"] == {"connect": 12}
    assert result["climate_operation_counts"] == {"connect": 1, "fetchall": 0}
    assert result["climate_before_service_ms"] == 1
    assert "climate_after_service_ms" not in result
    assert "secret" not in json.dumps(result)


def test_threadpool_timings_reach_existing_completion_event(monkeypatch, caplog):
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    caplog.set_level(logging.INFO)
    client = TestClient(
        create_app(settings=ApiSettings(), observation_repository=FixtureRepository())
    )
    response = client.get(
        "/v1/observations", params=params(), headers={"X-Request-ID": "climate-243"}
    )
    assert response.status_code == 200
    events = [r.context for r in caplog.records if r.message == "api_request_completed"]
    event = events[-1]
    assert event["request_id"] == "climate-243"
    assert set(event["climate_timing_ms"]) == timing.PHASES
    assert event["climate_operation_counts"]["service"] == 1
    assert event["climate_before_service_ms"] >= 0
    assert event["climate_after_service_ms"] >= 0
    assert timing.CURRENT.get() is None
    assert "fixture-release" not in json.dumps(event)
    client.get("/health/live")
    latest = [r.context for r in caplog.records if r.message == "api_request_completed"][-1]
    assert "climate_timing_ms" not in latest


def test_concurrent_recorders_do_not_leak():
    def run(number):
        recorder = timing.ClimateTiming()
        with timing.climate_request_timing(recorder):
            for _ in range(number):
                with dependency_span("snowflake", "execute"):
                    pass
        assert timing.CURRENT.get() is None
        return recorder.counts

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(1, 9)))
    assert [r["execute"] for r in results] == list(range(1, 9))
