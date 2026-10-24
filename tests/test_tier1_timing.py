"""Tier 1 request timing spans and structured completion fields."""

import json

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lyme_gap_atlas_api import tier1_timing as timing
from lyme_gap_atlas_api.dependency_telemetry import dependency_span
from lyme_gap_atlas_api.telemetry_logging import completion_context


def test_tier1_phase_and_dependency_nesting(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    clock = [0.0]
    monkeypatch.setattr(timing, "perf_counter", lambda: clock[0])
    monkeypatch.setattr("lyme_gap_atlas_api.dependency_telemetry.perf_counter", lambda: clock[0])
    recorder = timing.Tier1Timing()
    with (
        trace.get_tracer("test").start_as_current_span("request"),
        timing.tier1_request_timing(recorder),
        timing.phase("tier1_query"),
        dependency_span("snowflake", "connect"),
    ):
        clock[0] = 0.05
    assert timing.CURRENT.get() is None
    assert recorder.durations["connect"] == 50
    assert recorder.durations["tier1_query"] == 50
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert "atlas.tier1.tier1_query" in spans
    assert "atlas.dependency.snowflake.connect" in spans
    assert "private" not in json.dumps([dict(s.attributes) for s in exporter.get_finished_spans()])


def test_completion_context_accepts_tier1_timing_ms():
    result = completion_context(
        "api_request_completed",
        {
            "tier1_timing_ms": {"connect": 12, "tier1_query": 40, "injected": 1},
            "tier1_operation_counts": {"connect": 1, "tier1_query": 1},
        },
    )
    assert result["tier1_timing_ms"] == {"connect": 12, "tier1_query": 40}
    assert result["tier1_operation_counts"] == {"connect": 1, "tier1_query": 1}
