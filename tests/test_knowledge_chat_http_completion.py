"""One actual HTTP completion and no raw exception export on chat failures."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

from fastapi.testclient import TestClient
from opentelemetry.instrumentation import fastapi as fastapi_instrumentation
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.knowledge_chat import Answerer, KnowledgeChatService, Retriever
from lyme_gap_atlas_api.models import KnowledgeChatRequest
from lyme_gap_atlas_api.repository import AtlasRepository

SECRET = "authorization-or-serialization-secret"


class FailingStore:
    def authorize(self, conversation_id: str, token_hash: str) -> bool:
        raise RuntimeError(SECRET)

    def reserve(self, request_id: str) -> bool:
        return True

    def persist(self, **kwargs: Any) -> None:
        pass


def _settings() -> ApiSettings:
    return ApiSettings(
        snowflake_account="test", snowflake_user="test", snowflake_role="test",
        snowflake_pat="test", knowledge_chat_enabled=True,
    )


def _completions(stderr: str) -> list[dict[str, Any]]:
    return [
        row["context"] for row in (json.loads(line) for line in stderr.splitlines())
        if row.get("message") == "knowledge_chat_total"
    ]


def _capture_http_spans(monkeypatch: Any) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        fastapi_instrumentation, "get_tracer", lambda *args, **kwargs: provider.get_tracer("test")
    )
    return exporter


def _assert_private_spans(exporter: InMemorySpanExporter) -> None:
    spans = exporter.get_finished_spans()
    assert spans
    rendered = " ".join(
        [str(span.attributes) + str(span.status.description) + str(span.events) for span in spans]
    )
    assert SECRET not in rendered
    assert "exception.stacktrace" not in rendered


def test_authorization_db_exception_reports_actual_500_once(capsys: Any, monkeypatch: Any) -> None:
    exporter = _capture_http_spans(monkeypatch)
    service = KnowledgeChatService(
        cast(Retriever, object()), cast(Answerer, object()), FailingStore(), SECRET
    )
    api = TestClient(
        create_app(cast(AtlasRepository, object()), _settings(), service),
        raise_server_exceptions=False,
    )
    capsys.readouterr()
    response = api.post(
        "/v1/knowledge-graph/chat", headers={"x-request-id": "auth-failure"},
        json=KnowledgeChatRequest(
            message="Question", conversation_id="conversation-1", conversation_token="capability"
        ).model_dump(mode="json"),
    )
    output = capsys.readouterr().err
    completions = _completions(output)
    assert response.status_code == 500
    assert len(completions) == 1
    assert completions[0]["http_status"] == 500
    assert completions[0]["outcome"] == "authorization_dependency_failure"
    assert SECRET not in output
    assert SECRET not in response.text
    _assert_private_spans(exporter)


def test_serialization_failure_overrides_answered_outcome(capsys: Any, monkeypatch: Any) -> None:
    exporter = _capture_http_spans(monkeypatch)
    class InvalidResponseService:
        def chat(
            self, request: KnowledgeChatRequest, request_id: str,
            network_identifier: str, completion: dict[str, Any]
        ) -> Any:
            completion.update({"outcome": "answered", "generation_attempts": 1})
            return SimpleNamespace(status="answered", secret=SECRET)

    service = cast(KnowledgeChatService, InvalidResponseService())
    api = TestClient(
        create_app(cast(AtlasRepository, object()), _settings(), service),
        raise_server_exceptions=False,
    )
    capsys.readouterr()
    response = api.post("/v1/knowledge-graph/chat", json={"message": "Question"})
    output = capsys.readouterr().err
    completions = _completions(output)
    assert response.status_code == 500
    assert len(completions) == 1
    assert completions[0]["http_status"] == 500
    assert completions[0]["outcome"] == "response_serialization_failure"
    assert SECRET not in output
    assert SECRET not in response.text
    _assert_private_spans(exporter)
