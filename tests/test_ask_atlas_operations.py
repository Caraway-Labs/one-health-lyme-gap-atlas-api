"""Operational boundaries through the actual assistant request middleware."""

import asyncio
import logging
from typing import Any

import pytest
from fastapi import Request, Response
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_ask_atlas_orchestration import FakeTools

from lyme_gap_atlas_api import app as application
from lyme_gap_atlas_api import middleware as middleware_module
from lyme_gap_atlas_api.ask_atlas_orchestration import (
    StructuredAssistant,
    StructuredAssistantRequest,
)
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.middleware import KnowledgeChatLimitMiddleware
from lyme_gap_atlas_api.telemetry_logging import (
    assistant_operational_outcome,
    completion_context,
)


def test_structured_http_rate_boundary_is_route_scoped() -> None:
    app = application.create_app(settings=ApiSettings(knowledge_chat_enabled=False))
    client = TestClient(app)
    payload = {"question": "Run SQL for county 08001", "source_mode": "Structured"}
    for _ in range(30):
        assert client.post("/v1/assistant/structured", json=payload).status_code == 200
    limited = client.post("/v1/assistant/structured", json=payload)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "600"
    assert client.post("/v1/assistant/mixed", json=payload).status_code != 429


def test_paid_literature_routes_share_one_rate_bucket() -> None:
    app = application.create_app(settings=ApiSettings(knowledge_chat_enabled=False))
    client = TestClient(app)
    payload = {
        "question": "What does the governed literature say about Lyme disease?",
        "source_mode": "Literature",
    }
    for _ in range(10):
        assert client.post("/v1/assistant/mixed", json=payload).status_code == 503
    assert client.post("/v1/knowledge-graph/chat", json={"message": "Question"}).status_code == 429


def test_paid_global_rate_ceiling_survives_rotating_client_identity() -> None:
    async def exercise() -> None:
        limiter = KnowledgeChatLimitMiddleware(object())

        async def next_request(_: Request) -> Response:
            return Response(status_code=200)

        for index in range(60):
            request = Request({
                "type": "http", "method": "POST", "path": "/v1/assistant/mixed",
                "query_string": b"", "headers": [], "client": (f"192.0.2.{index}", 1234),
                "scheme": "http", "server": ("test", 80),
            })
            assert (await limiter.dispatch(request, next_request)).status_code == 200
        overflow = Request({
            "type": "http", "method": "POST", "path": "/v1/assistant/mixed",
            "query_string": b"", "headers": [], "client": ("198.51.100.1", 1234),
            "scheme": "http", "server": ("test", 80),
        })
        assert (await limiter.dispatch(overflow, next_request)).status_code == 429

    asyncio.run(exercise())


def test_paid_global_concurrency_ceiling_releases_slots() -> None:
    async def exercise() -> None:
        limiter = KnowledgeChatLimitMiddleware(object())
        release = asyncio.Event()
        entered = asyncio.Event()
        count = 0

        def request(index: int) -> Request:
            return Request({
                "type": "http", "method": "POST", "path": "/v1/assistant/mixed",
                "query_string": b"", "headers": [], "client": (f"192.0.2.{index}", 1234),
                "scheme": "http", "server": ("test", 80),
            })

        async def next_request(_: Request) -> Response:
            nonlocal count
            count += 1
            if count == 6:
                entered.set()
            await release.wait()
            return Response(status_code=200)

        tasks = [asyncio.create_task(limiter.dispatch(request(index), next_request))
                 for index in range(6)]
        await asyncio.wait_for(entered.wait(), 2)
        assert (await limiter.dispatch(request(7), next_request)).status_code == 429
        release.set()
        assert all(item.status_code == 200 for item in await asyncio.gather(*tasks))
        assert (await limiter.dispatch(request(8), next_request)).status_code == 200

    asyncio.run(exercise())


def test_assistant_concurrency_slot_released_after_response() -> None:
    async def exercise() -> None:
        limiter = KnowledgeChatLimitMiddleware(object())
        release = asyncio.Event()
        entered = asyncio.Event()
        count = 0

        def request() -> Request:
            return Request({
                "type": "http", "method": "POST", "path": "/v1/assistant/structured",
                "query_string": b"", "headers": [], "client": ("192.0.2.1", 1234),
                "scheme": "http", "server": ("test", 80),
            })

        async def next_request(_: Request) -> Response:
            nonlocal count
            count += 1
            if count == 3:
                entered.set()
            await release.wait()
            return Response(status_code=200)

        tasks = [asyncio.create_task(limiter.dispatch(request(), next_request))
                 for _ in range(3)]
        await asyncio.wait_for(entered.wait(), 2)
        assert (await limiter.dispatch(request(), next_request)).status_code == 429
        release.set()
        assert [r.status_code for r in await asyncio.gather(*tasks)] == [200] * 3
        assert (await limiter.dispatch(request(), next_request)).status_code == 200

    asyncio.run(exercise())


def test_closed_operational_classes_and_private_completion() -> None:
    for outcome, cause, expected in (
        ("answered", None, "answered"),
        ("insufficient_evidence", None, "abstained"),
        ("source_unavailable", "neo4j_timeout", "dependency_failure"),
        ("source_unavailable", "provider_rejection", "provider_failure"),
        ("source_unavailable", "budget_exhausted", "budget_exhaustion"),
        ("source_unavailable", "grounding_validation_failed", "validation_failure"),
        ("answered", "unhandled_error", "internal_failure"),
        ("source_unavailable", "unhandled_error", "internal_failure"),
        ("route_unavailable", None, "dependency_failure"),
        ("unhandled_error", None, "internal_failure"),
    ):
        assert assistant_operational_outcome(outcome, cause) == expected
    assert assistant_operational_outcome(
        "source_unavailable", "source_unavailable", "tool_validation_failure"
    ) == "validation_failure"
    sanitized = completion_context("knowledge_chat_total", {
        "request_id": "fixture-request", "outcome": "source_unavailable",
        "operational_outcome": "provider_failure", "service_outcome": "provider_rejection",
        "structured_cause": "private cause", "question": "private prompt",
        "evidence": "private passage",
        "provider_cost_usd": 0,
    })
    assert sanitized["operational_outcome"] == "provider_failure"
    assert sanitized["service_outcome"] == "provider_rejection"
    assert "private" not in str(sanitized)
    assert "provider_cost_usd" not in sanitized


@pytest.mark.parametrize(("failure", "cause", "operational"), [
    ("timeout", "tool_timeout", "dependency_failure"),
    ("malformed", "tool_validation_failure", "validation_failure"),
    ("internal", "tool_internal_failure", "internal_failure"),
])
def test_structured_http_classifies_actual_tool_boundary_failure(
    failure: str, cause: str, operational: str, monkeypatch,
) -> None:
    completions: list[dict[str, Any]] = []

    def capture(logger, event, context):
        if event == "knowledge_chat_total":
            completions.append(completion_context(event, context))

    class InternalTools(FakeTools):
        def get_observations(self, raw):
            raise RuntimeError("private internal detail")

    tools = InternalTools() if failure == "internal" else FakeTools()
    if failure == "timeout":
        tools.fail_on = "get_observations"
    elif failure == "malformed":
        tools.result_override = {"tool": "get_observations", "status": "ok",
                                 "private": "evidence body"}
    monkeypatch.setattr(application, "StructuredTools", lambda *args: tools)
    monkeypatch.setattr(middleware_module, "emit_completion", capture)
    app = application.create_app(settings=ApiSettings(knowledge_chat_enabled=False))
    response = TestClient(app).post(
        "/v1/assistant/structured", headers={"X-Request-ID": "tool-fixture"},
        json={
            "question": "What is the 2023 Lyme case count for county 08001?",
            "context": {"measure_id": "case_count_floor_2023",
                        "geography_ids": ["08001"], "year": 2023},
        },
    )
    assert response.status_code == 200
    assert response.json()["answer"]["outcome"] == "SOURCE_UNAVAILABLE"
    assert response.json()["answer"]["claims"] == []
    assert response.json()["tool_evidence"][0]["error_code"] == (
        None if failure == "malformed" else "SOURCE_UNAVAILABLE"
    )
    assert len(completions) == 1
    assert completions[0]["structured_cause"] == cause
    assert completions[0]["operational_outcome"] == operational
    assert "private" not in str(completions)


def test_structured_span_rejects_caller_supplied_cause(monkeypatch) -> None:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda name: provider.get_tracer(name))
    diagnostics = {"structured_cause": "private evidence content"}
    result = StructuredAssistant(FakeTools()).ask(
        StructuredAssistantRequest(
            question="What is the 2023 Lyme case count for county 08001?",
            context={"measure_id": "case_count_floor_2023",
                     "geography_ids": ["08001"], "year": 2023},
        ), diagnostics,
    )
    assert result.answer.outcome == "ANSWERED"
    assert "structured_cause" not in diagnostics
    assert "private" not in str([span.attributes for span in exporter.get_finished_spans()])


def test_structured_request_survives_broken_trace_export(monkeypatch, caplog) -> None:
    secret = "private-prompt-evidence-token"

    class BrokenExporter(SpanExporter):
        def export(self, spans):
            raise RuntimeError(secret)

        def shutdown(self) -> None:
            pass

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(BrokenExporter()))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    caplog.set_level(logging.INFO)
    app = application.create_app(settings=ApiSettings(knowledge_chat_enabled=False))
    response = TestClient(app).post(
        "/v1/assistant/structured", headers={"X-Request-ID": "export-fixture"},
        json={"question": "Run SQL for county 08001", "source_mode": "Structured"},
    )
    assert response.status_code == 200
    assert response.json()["answer"]["outcome"] == "SAFETY_REFUSAL"
    assert response.headers["X-Request-ID"] == "export-fixture"
    assert secret not in caplog.text
