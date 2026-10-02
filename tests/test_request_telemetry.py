"""Request correlation survives real FastAPI instrumentation without unsafe data."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.middleware import RequestContextMiddleware

SECRET = "private-question-evidence-token-198.51.100.99"


@pytest.fixture
def instrumented(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    api = create_app(settings=ApiSettings())

    @api.get("/test-error")
    def error():
        raise RuntimeError(SECRET)

    @api.get("/test-child")
    def child():
        with trace.get_tracer("test").start_as_current_span("dependency.test"):
            return {"ok": True}

    return TestClient(api, raise_server_exceptions=False), exporter


@pytest.mark.parametrize("supplied", ["operator-123", "", "invalid value", "x" * 81])
@pytest.mark.parametrize(
    "path,status",
    [
        ("/health/live", 200),
        ("/v1/geographies/county/01001", 503),
        (f"/unknown/{SECRET}?q={SECRET}", 404),
        ("/test-error", 500),
    ],
)
def test_request_root_and_redaction(instrumented, caplog, supplied, path, status):
    client, exporter = instrumented
    caplog.set_level(logging.INFO)
    response = client.get(
        path, headers={"X-Request-ID": supplied, "Authorization": f"Bearer {SECRET}"}
    )
    assert response.status_code == status
    correlation = response.headers["X-Request-ID"]
    assert (correlation == supplied) == (supplied == "operator-123")
    roots = [s for s in exporter.get_finished_spans() if s.kind == trace.SpanKind.SERVER]
    assert len(roots) == 1
    attrs = dict(roots[0].attributes)
    assert attrs["request.id"] == correlation
    assert attrs["http.status_code"] == status
    assert attrs["atlas.request.status_class"] == f"{status // 100}xx"
    assert attrs["http.route"] == (
        "unmatched"
        if status in {404, 500}
        else "/v1/geographies/{geography_type}/{geography_id}"
        if status == 503
        else path
    )
    payload = json.dumps(
        [
            {
                "name": s.name,
                "attributes": dict(s.attributes),
                "events": [str(e) for e in s.events],
                "status": s.status.description,
            }
            for s in exporter.get_finished_spans()
        ]
    )
    assert SECRET not in payload
    assert "01001" not in payload
    events = [
        r.context
        for r in caplog.records
        if r.msg in {"api_request_completed", "api_request_failed"}
    ]
    assert events[-1]["request_id"] == correlation
    assert events[-1]["path"] == attrs["http.route"]
    assert events[-1]["outcome"] == attrs["atlas.request.outcome"]


def test_w3c_context_preserved(instrumented):
    client, exporter = instrumented
    response = client.get(
        "/health/live",
        headers={
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
        },
    )
    assert response.status_code == 200
    root = next(s for s in exporter.get_finished_spans() if s.kind == trace.SpanKind.SERVER)
    assert root.context.trace_id == int("0123456789abcdef0123456789abcdef", 16)
    assert root.parent.span_id == int("0123456789abcdef", 16)


def test_child_span_uses_canonical_request_parent(instrumented):
    client, exporter = instrumented
    assert client.get("/test-child").status_code == 200
    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.kind == trace.SpanKind.SERVER)
    child = next(s for s in spans if s.name == "dependency.test")
    assert child.parent.span_id == root.context.span_id
    assert child.context.trace_id == root.context.trace_id


def test_concurrent_context_isolation(instrumented):
    client, exporter = instrumented
    ids = [f"parallel-{i}" for i in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(
            pool.map(
                lambda identity: client.get("/health/live", headers={"X-Request-ID": identity}),
                ids,
            )
        )
    assert [r.headers["X-Request-ID"] for r in responses] == ids
    roots = [s for s in exporter.get_finished_spans() if s.kind == trace.SpanKind.SERVER]
    assert {s.attributes["request.id"] for s in roots} == set(ids)
    assert len({s.context.trace_id for s in roots}) == len(ids)


def test_instrumentation_start_failure_is_optional(monkeypatch):
    class BrokenProvider:
        def get_tracer(self, *args, **kwargs):
            return self

        def start_span(self, *args, **kwargs):
            raise RuntimeError(SECRET)

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: BrokenProvider())
    client = TestClient(create_app(settings=ApiSettings()))
    response = client.get("/health/live", headers={"X-Request-ID": "still-safe"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "still-safe"


def test_cancellation_propagates_without_fabricated_response(instrumented, caplog):
    _, exporter = instrumented
    caplog.set_level(logging.INFO)

    async def cancelled(request):
        raise asyncio.CancelledError()

    with trace.get_tracer("test").start_as_current_span("cancelled-request") as span:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/health/live",
                "headers": [],
                "scheme": "http",
                "state": {"request_id": "cancel-safe", "request_span": span},
            }
        )
        middleware = RequestContextMiddleware(lambda: None)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(middleware.dispatch(request, cancelled))
    finished = exporter.get_finished_spans()[-1]
    assert finished.attributes["atlas.request.outcome"] == "cancelled"
    assert "http.status_code" not in finished.attributes
    event = next(r.context for r in caplog.records if r.msg == "api_request_failed")
    assert event["outcome"] == "cancelled"
    assert "status_code" not in event


def test_exporter_failure_preserves_response(monkeypatch, caplog):
    from opentelemetry.sdk.trace.export import SpanExporter

    class BrokenExporter(SpanExporter):
        def export(self, spans):
            raise RuntimeError(SECRET)

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(BrokenExporter()))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    client = TestClient(create_app(settings=ApiSettings()))
    caplog.set_level(logging.INFO)
    response = client.get("/health/live", headers={"X-Request-ID": "export-safe"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "export-safe"
    assert SECRET not in caplog.text


def test_ask_atlas_http_ancestry_and_contract(instrumented):
    from test_knowledge_chat_diagnostics import (
        EVIDENCE,
        Answerer,
        Retriever,
        candidate,
    )

    from lyme_gap_atlas_api.knowledge_chat import KnowledgeChatService

    _, exporter = instrumented
    service = KnowledgeChatService(
        Retriever(EVIDENCE), Answerer([candidate()]), None, "test-placeholder"
    )
    client = TestClient(
        create_app(
            settings=ApiSettings(knowledge_chat_enabled=True), knowledge_chat_service=service
        )
    )
    response = client.post(
        "/v1/knowledge-graph/chat",
        json={"message": "Explain corpus evidence"},
        headers={
            "X-Request-ID": "chat-correlation",
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    finished = exporter.get_finished_spans()
    roots = [s for s in finished if s.kind == trace.SpanKind.SERVER]
    assert len(roots) == 1
    root = roots[0]
    assert root.attributes["request.id"] == "chat-correlation"
    assert root.context.trace_id == int("0123456789abcdef0123456789abcdef", 16)
    assert root.parent.span_id == int("0123456789abcdef", 16)
    assert all(s.context.trace_id == root.context.trace_id for s in finished)
    ids = {s.context.span_id for s in finished}
    assert all(s.parent.span_id in ids for s in finished if s is not root)
    boundaries = [s for s in finished if s.name == "knowledge_chat.service"]
    # PR158 is independently owned: when integrated, verify its unchanged boundary.
    if boundaries:
        assert len(boundaries) == 1
        assert boundaries[0].parent.span_id == root.context.span_id
        assert boundaries[0].attributes["atlas.ask_atlas.outcome"] == "answered"
        assert boundaries[0].attributes["atlas.ask_atlas.outcome_class"] == "answered"
