"""Health scope stays independent of optional feature and exporter health."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.repository import Snapshot


class ProbeRepository:
    def __init__(self, outcome: bool | Exception = True) -> None:
        self.outcome = outcome
        self.probes = 0

    def ready(self) -> bool:
        self.probes += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def load_snapshot(self) -> Snapshot:
        raise AssertionError("Health must not load a data snapshot")


@pytest.fixture(autouse=True)
def isolated_health_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.setenv("UVICORN_WORKERS", "1")
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)


@pytest.mark.parametrize("outcome", [False, RuntimeError("private dependency detail")])
def test_liveness_survives_critical_dependency_failure(outcome: bool | Exception) -> None:
    repository = ProbeRepository(outcome)
    with TestClient(create_app(repository, ApiSettings())) as api:
        live = api.get("/health/live")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        assert repository.probes == 0
        ready = api.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["detail"] == "A required data service is unavailable"
        assert "private dependency detail" not in ready.text
        assert repository.probes == 1
        assert api.get("/health/live").status_code == 200
        assert repository.probes == 1


def test_enabled_but_unwired_chat_is_configuration_not_process_failure() -> None:
    repository = ProbeRepository()
    settings = ApiSettings(knowledge_chat_enabled=True)
    with TestClient(create_app(repository, settings)) as api:
        assert api.get("/health/ready").status_code == 503
        assert api.get("/health/live").status_code == 200


def test_optional_accounts_and_features_do_not_gate_public_readiness() -> None:
    repository = ProbeRepository()
    settings = ApiSettings(knowledge_chat_enabled=False)
    with TestClient(create_app(repository, settings)) as api:
        assert api.get("/v1/me/profile").status_code == 503
        ready = api.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}
        assert repository.probes == 1


@pytest.mark.parametrize("worker_variable", ["WEB_CONCURRENCY", "UVICORN_WORKERS"])
def test_unsafe_feedback_topology_is_unready_but_live(
    worker_variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(worker_variable, "2")
    repository = ProbeRepository()
    with TestClient(create_app(repository, ApiSettings())) as api:
        assert api.get("/health/ready").status_code == 503
        assert repository.probes == 0
        assert api.get("/health/live").status_code == 200


def test_exporter_initialization_failure_does_not_gate_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lyme_gap_atlas_shared import observability

    def unavailable_exporter(**kwargs: Any) -> None:
        raise RuntimeError("fake exporter unavailable")

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://example.invalid")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "")
    monkeypatch.setattr(observability, "_tracer_provider", None)
    monkeypatch.setattr(observability, "OTLPSpanExporter", unavailable_exporter)
    repository = ProbeRepository()
    with TestClient(create_app(repository, ApiSettings())) as api:
        assert api.get("/health/live").status_code == 200
        assert api.get("/health/ready").json() == {"status": "ready"}
        assert repository.probes == 1
