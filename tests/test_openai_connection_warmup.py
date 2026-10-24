"""Idle-surviving OpenAI connections, a harmless warm-up, and provider timing logs (#306)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI
from pydantic import SecretStr

from lyme_gap_atlas_api import app as app_module
from lyme_gap_atlas_api import knowledge_chat as chat
from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.repository import AtlasRepository

WAIT_SECONDS = 5.0


def response(path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    request = httpx.Request("POST", f"https://api.openai.com{path}")
    return httpx.Response(200, headers=headers or {}, request=request)


def test_client_keeps_idle_connections_and_logs_provider_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_default_client(**kwargs: Any) -> httpx.Client:
        captured.update(kwargs)
        return httpx.Client()

    monkeypatch.setattr(chat, "DefaultHttpxClient", fake_default_client)
    chat.build_openai_http_client()
    limits = captured["limits"]
    assert limits.keepalive_expiry == 120
    assert limits.max_keepalive_connections == 10
    assert captured["event_hooks"] == {
        "request": [chat._attach_trace],
        "response": [chat._log_provider_response],
    }


def test_with_options_copies_share_the_warm_pool() -> None:
    pool = chat.build_openai_http_client()
    client = OpenAI(api_key="test-key", http_client=pool)
    assert client.with_options(max_retries=0, timeout=5)._client is pool


def test_provider_timing_is_logged_for_chat_paths_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    chat._log_provider_response(response("/v1/embeddings", {"openai-processing-ms": "123"}))
    chat._log_provider_response(response("/v1/responses", {"openai-processing-ms": "n/a"}))
    chat._log_provider_response(response("/v1/models", {"openai-processing-ms": "9"}))
    events = [r for r in caplog.records if r.getMessage() == "knowledge_chat_provider_http"]
    contexts = [r.context for r in events]  # type: ignore[attr-defined]
    assert [c["path"] for c in contexts] == ["/v1/embeddings", "/v1/responses"]
    assert [c["openai_processing_ms"] for c in contexts] == [123, None]
    fields = {"request_id", "path", "status_code", "openai_processing_ms"}
    assert all(set(c) == fields for c in contexts)


def test_warmer_pings_repeatedly_and_stops() -> None:
    calls = threading.Semaphore(0)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.release()
        return httpx.Response(401, json={"error": "unauthenticated"})

    warmer = chat.OpenAIConnectionWarmer(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "https://api.openai.com/v1/models",
        interval_seconds=0.05,
    )
    warmer.start()
    warmer.start()
    assert calls.acquire(timeout=WAIT_SECONDS)
    assert calls.acquire(timeout=WAIT_SECONDS)
    warmer.stop()
    assert warmer._thread is not None
    assert not warmer._thread.is_alive()


def test_warm_failure_is_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private network detail")

    warmer = chat.OpenAIConnectionWarmer(
        httpx.Client(transport=httpx.MockTransport(handler)), "https://api.openai.com/v1/models"
    )
    warmer.warm()
    warmer.stop()
    assert "private network detail" not in caplog.text


def test_app_starts_and_stops_the_warmer_only_when_chat_is_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeWarmer:
        def __init__(self, http_client: httpx.Client, url: str) -> None:
            events.append(f"init {url}")

        def start(self) -> None:
            events.append("start")

        def stop(self) -> None:
            events.append("stop")

    class FakeRetriever:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def ready(self) -> bool:
            return True

    monkeypatch.setattr(app_module, "OpenAIConnectionWarmer", FakeWarmer)
    monkeypatch.setattr(app_module, "Neo4jRetriever", FakeRetriever)
    settings = ApiSettings(
        snowflake_account="test",
        snowflake_user="test",
        snowflake_role="test",
        snowflake_pat="test",
        knowledge_chat_enabled=True,
        neo4j_uri="bolt://neo4j.test:7687",
        neo4j_runtime_password=SecretStr("password"),
        openai_api_key=SecretStr("sk-test"),
        kg_hash_secret=SecretStr("hash"),
    )
    with TestClient(create_app(cast(AtlasRepository, object()), settings)):
        assert events == ["init https://api.openai.com/v1/models", "start"]
    assert events[-1] == "stop"

    events.clear()
    disabled = settings.model_copy(update={"knowledge_chat_enabled": False})
    with TestClient(create_app(cast(AtlasRepository, object()), disabled)):
        pass
    assert events == []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        body = b'{"error":"unauthenticated"}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("openai-processing-ms", "7")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def local_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def tracing_client() -> httpx.Client:
    return httpx.Client(
        event_hooks={
            "request": [chat._attach_trace],
            "response": [chat._log_provider_response],
        }
    )


def test_trace_callback_records_connection_phases() -> None:
    trace = chat._ConnectionTrace()
    assert trace.new_connection is False
    assert trace.milliseconds("connection.connect_tcp") is None
    trace("connection.connect_tcp.started", {})
    trace("connection.connect_tcp.complete", {})
    trace("connection.start_tls.started", {})
    trace("connection.start_tls.failed", {})
    trace("http11.send_request_headers.started", {})
    assert trace.new_connection is True
    assert trace.milliseconds("connection.connect_tcp") is not None
    assert trace.milliseconds("connection.start_tls") is not None
    assert trace.milliseconds("http11.receive_response_headers") is None


def test_provider_log_shows_new_then_reused_connection(
    local_server: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    with tracing_client() as client:
        client.get(f"{local_server}/v1/embeddings")
        client.get(f"{local_server}/v1/embeddings")
        client.get(f"{local_server}/v1/models")
    events = [r.context for r in caplog.records if r.getMessage() == "knowledge_chat_provider_http"]  # type: ignore[attr-defined]
    assert [e["new_connection"] for e in events] == [True, False]
    assert events[0]["connect_ms"] is not None
    assert events[1]["connect_ms"] is None
    assert events[0]["openai_processing_ms"] == 7
    assert all(e["headers_wait_ms"] is not None for e in events)


def test_warmer_logs_new_connections_at_info_and_reuse_at_debug(
    local_server: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with tracing_client() as client:
        warmer = chat.OpenAIConnectionWarmer(client, f"{local_server}/v1/models")
        warmer.warm()
        warmer.warm()
    warm = [r for r in caplog.records if r.getMessage() == "openai_connection_warm"]
    assert [r.levelno for r in warm] == [logging.INFO, logging.DEBUG]
    assert [r.context["new_connection"] for r in warm] == [True, False]  # type: ignore[attr-defined]
    assert all(r.context["status_code"] == 401 for r in warm)  # type: ignore[attr-defined]


def test_warm_failure_is_logged_at_info_without_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private network detail")

    warmer = chat.OpenAIConnectionWarmer(
        httpx.Client(transport=httpx.MockTransport(handler)), "https://api.openai.com/v1/models"
    )
    warmer.warm()
    failed = [r for r in caplog.records if r.getMessage() == "openai_connection_warm_failed"]
    assert len(failed) == 1
    assert "private network detail" not in caplog.text
    assert "error_type" in failed[0].context  # type: ignore[attr-defined]
