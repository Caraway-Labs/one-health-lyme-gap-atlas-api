"""Closed completion schemas exclude sensitive content even from faulty diagnostics."""

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api import dependency_telemetry
from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.telemetry import request_dimensions
from lyme_gap_atlas_api.telemetry_logging import (
    completion_context,
    emit_completion,
    operational_logger,
    protect_dependency_logs,
)

SENSITIVE = (
    "question",
    "prompt",
    "body",
    "response",
    "evidence",
    "support_quote",
    "token",
    "credentials",
    "query",
    "sql",
    "exception_message",
    "user_id",
    "client_ip",
    "provider_request_id",
    "query_id",
    "model_id",
)
SECRET = "private-sensitive-value-198.51.100.20"


@pytest.mark.parametrize(
    "event", ["api_request_completed", "api_request_failed", "knowledge_chat_total"]
)
def test_closed_completion_schema(event):
    raw = {key: SECRET for key in SENSITIVE}
    raw.update(
        {
            "request_id": "safe-correlation",
            "outcome": SECRET,
            "method": SECRET,
            "path": f"/{SECRET}",
            "failure_class": SECRET,
            "configuration_version": SECRET,
            "retrieval_version": SECRET,
            "evidence_state": SECRET,
            "validation_outcome": SECRET,
            "generation_attempts": 999,
            "retrieval_passage_count": 10**10,
            "stage_latencies_ms": {SECRET: 10, "embedding": 20},
            "diagnostic": SECRET,
        }
    )
    result = completion_context(event, raw)
    assert SECRET not in json.dumps(result)
    assert result["request_id"] == "safe-correlation"
    assert result["path"] == "unmatched"
    assert result["generation_attempts"] == 2
    assert result["retrieval_passage_count"] == 100
    assert result["stage_latencies_ms"] == {"embedding": 20}
    assert result["telemetry_schema_version"] == "1"


def test_cardinality_and_unknown_route():
    for index in range(100):
        dimensions = request_dimensions(f"METHOD-{index}", f"/users/{index}", 503, f"Error{index}")
        assert dimensions == request_dimensions("OTHER", "unmatched", 503, "unhandled_error")


def test_logging_failure_does_not_escape():
    class BrokenLogger:
        def log(self, *args, **kwargs):
            raise RuntimeError(SECRET)

    emit_completion(BrokenLogger(), "api_request_completed", {"request_id": "safe"})


def test_application_handler_failure_does_not_escape(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(logging.getLogger("test-safe-logger"), "log", broken)
    logger = operational_logger("test-safe-logger")
    logger.warning("test_event", extra={"context": {"request_id": "safe"}})


def test_sdk_logs_cannot_emit_url_or_exception(caplog):
    caplog.set_level(logging.INFO)
    protect_dependency_logs()
    logging.getLogger("httpx").info("GET https://example.test/%s?q=%s", SECRET, SECRET)
    logging.getLogger("snowflake.connector").warning("Query %s", SECRET)
    logging.getLogger("opentelemetry.sdk.trace.export").error(
        "Export %s", SECRET, exc_info=RuntimeError(SECRET)
    )
    assert SECRET not in caplog.text
    assert any(r.msg == "dependency_sdk_warning" for r in caplog.records)
    assert any(r.msg == "telemetry_backend_warning" for r in caplog.records)


@pytest.mark.parametrize("dedicated_error_handler", [False, True])
def test_uvicorn_stderr_hierarchy_cannot_emit_exception_chain(monkeypatch, dedicated_error_handler):
    from uvicorn.logging import DefaultFormatter

    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(DefaultFormatter(fmt="%(levelprefix)s %(message)s", use_colors=False))
    parent = logging.getLogger("uvicorn")
    error = logging.getLogger("uvicorn.error")
    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(parent, "handlers", [handler])
    monkeypatch.setattr(parent, "propagate", False)
    monkeypatch.setattr(error, "handlers", [handler] if dedicated_error_handler else [])
    monkeypatch.setattr(error, "propagate", not dedicated_error_handler)
    monkeypatch.setattr(access, "handlers", [handler])
    monkeypatch.setattr(access, "propagate", False)
    error.setLevel(logging.INFO)
    access.setLevel(logging.INFO)
    protect_dependency_logs()
    try:
        try:
            raise ValueError(SECRET)
        except ValueError as original:
            raise RuntimeError(SECRET) from original
    except RuntimeError:
        error.exception("Exception in ASGI application %s", SECRET)
    access.info("GET /private/%s?token=%s", SECRET, SECRET)
    text = output.getvalue()
    assert "api_server_error" in text
    assert SECRET not in text
    assert "Traceback" not in text
    assert "ValueError" not in text


def test_readiness_error_text_and_codes_not_logged(monkeypatch, caplog):
    class UnsafeError(RuntimeError):
        errno = SECRET
        sqlstate = SECRET

    def fail(*args, **kwargs):
        raise UnsafeError(SECRET)

    monkeypatch.setattr(dependency_telemetry, "shared_connect", fail)
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    caplog.set_level(logging.INFO)
    client = TestClient(create_app(settings=ApiSettings()))
    assert client.get("/health/ready").status_code == 503
    assert client.get("/health/live").status_code == 200
    event = next(r for r in caplog.records if r.msg == "atlas_readiness_check_failed")
    assert event.context == {"failure_class": "dependency_error"}
    assert SECRET not in caplog.text


@pytest.mark.parametrize("suffix", ["", "/confirm", "/export"])
@pytest.mark.parametrize("tracing", [True, False])
def test_privacy_failure_correlates_http_request_not_resource(monkeypatch, caplog, suffix, tracing):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from test_privacy_requests import _api

    from lyme_gap_atlas_api.privacy_requests import (
        MemoryPrivacyRequestStore,
        PrivacyRequestStoreError,
    )

    class UnavailableStore(MemoryPrivacyRequestStore):
        def get(self, request_id):
            raise PrivacyRequestStoreError("get", "upstream_http", 503)

    provider = TracerProvider() if tracing else trace.NoOpTracerProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    caplog.set_level(logging.INFO)
    api, _, _, _ = _api(store=UnavailableStore())
    resource_id = "33333333-3333-3333-3333-333333333333"
    headers = {"Authorization": "Bearer test-token", "X-Request-ID": "http-correlation"}
    path = f"/v1/me/privacy-requests/{resource_id}{suffix}"
    response = (
        api.post(path, headers=headers, json={"nonce": "n" * 32})
        if suffix == "/confirm"
        else (api.get(path, headers=headers))
    )
    assert response.status_code == 503
    events = [
        r.context
        for r in caplog.records
        if r.msg
        in {
            "privacy_request_status_failed",
            "privacy_request_confirm_failed",
            "privacy_export_download_failed",
        }
    ]
    assert events[-1]["request_id"] == "http-correlation"
    assert resource_id not in caplog.text
