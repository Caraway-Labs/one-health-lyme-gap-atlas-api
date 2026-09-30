"""Operational logs keep stable route labels without client identifiers."""

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings


def test_public_request_log_uses_route_template(caplog, monkeypatch) -> None:
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    caplog.set_level(logging.INFO, logger="lyme_gap_atlas_api.middleware")
    api = TestClient(create_app(settings=ApiSettings()))
    response = api.get("/v1/geographies/county/01001")
    assert response.status_code == 503
    events = [record.context for record in caplog.records if record.msg == "api_request_completed"]
    assert events[-1]["path"] == "/v1/geographies/{geography_type}/{geography_id}"
    assert "01001" not in str(events[-1])


def test_runtime_access_log_is_disabled() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert '"--no-access-log"' in dockerfile
