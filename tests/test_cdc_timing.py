"""Deterministic clocks, failure retention and bounded telemetry schema."""

import json

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lyme_gap_atlas_api import cdc_timing as timing
from lyme_gap_atlas_api.dependency_telemetry import dependency_span
from lyme_gap_atlas_api.telemetry_logging import completion_context


def test_clock_counts_nested_spans_failure_and_redaction(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    clock = [0.0]
    monkeypatch.setattr(timing, "perf_counter", lambda: clock[0])
    monkeypatch.setattr("lyme_gap_atlas_api.dependency_telemetry.perf_counter", lambda: clock[0])
    recorder = timing.CDCTiming()
    with (
        trace.get_tracer("test").start_as_current_span("request"),
        pytest.raises(TimeoutError),
        timing.cdc_request_timing(recorder),
    ):
        for name in ["connect", "execute", "fetchmany", "connection_teardown"]:
            with dependency_span("snowflake", name):
                clock[0] += 0.125
        with timing.phase("authority_query"), dependency_span("snowflake", "execute"):
            clock[0] += 0.125
            raise TimeoutError("private SQL bound FIPS and credentials")
    assert timing.CURRENT.get() is None
    assert recorder.durations == {
        "connect": 125,
        "execute": 250,
        "fetchmany": 125,
        "connection_teardown": 125,
        "authority_query": 125,
        "service": 625,
    }
    assert recorder.counts["execute"] == 2
    spans = exporter.get_finished_spans()
    failed = next(
        s
        for s in spans
        if s.name == "atlas.dependency.snowflake.execute"
        and s.status.is_ok is False
        and s.attributes.get("atlas.dependency.outcome") == "failure"
    )
    assert failed.attributes["atlas.dependency.failure_class"] == "timeout"
    authority = next(s for s in spans if s.name == "atlas.cdc.authority_query")
    assert failed.parent.span_id == authority.context.span_id
    assert not failed.events and not authority.events
    assert all(
        set(s.attributes)
        <= {
            "atlas.dependency.system",
            "atlas.dependency.operation",
            "atlas.dependency.outcome",
            "atlas.dependency.failure_class",
        }
        for s in spans
    )


def test_completion_schema_rejects_private_names_nonfinite_and_unbounded_counters():
    result = completion_context(
        "api_request_completed",
        {
            "request_id": "safe-correlation",
            "cdc_timing_ms": {
                "connect": 12.5,
                "execute": float("nan"),
                "fetchmany": float("inf"),
                "01001": 123,
                "private SQL": 123,
                "mapping": -2,
            },
            "cdc_operation_counts": {"connect": 1, "execute": 100_001, "private": 1},
            "cdc_before_service_ms": 2,
            "cdc_after_service_ms": 3,
        },
    )
    assert result["cdc_timing_ms"] == {"connect": 12, "mapping": 0}
    assert result["cdc_operation_counts"] == {"connect": 1, "execute": 10_000}
    assert "private" not in json.dumps(result) and "01001" not in json.dumps(result)


def test_optional_tracer_failure_cannot_change_result_or_leak_context(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private exporter configuration")

    monkeypatch.setattr(trace, "get_tracer", fail)
    recorder = timing.CDCTiming()
    with timing.cdc_request_timing(recorder), timing.phase("producer"):
        timing.record_processing(0.01)
    assert recorder.counts == {"producer_processing": 1, "producer": 1, "service": 1}
    assert timing.CURRENT.get() is None
