"""County-context observation request timing on /v1/observations."""

import json
import logging

from fastapi.testclient import TestClient
from test_county_context import Repository, params

from lyme_gap_atlas_api import context_timing as timing
from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.telemetry_logging import completion_context


def test_completion_context_accepts_context_timing_ms():
    result = completion_context(
        "api_request_completed",
        {
            "context_timing_ms": {"execute": 12.5, "service": 20.0, "privateSQL": 2},
            "context_operation_counts": {"execute": 1, "service": 1},
        },
    )
    assert result["context_timing_ms"] == {"execute": 12, "service": 20}
    assert result["context_operation_counts"] == {"execute": 1, "service": 1}


def test_context_observations_emit_timing_fields(monkeypatch, caplog):
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    caplog.set_level(logging.INFO)
    client = TestClient(
        create_app(settings=ApiSettings(), observation_repository=Repository())
    )
    response = client.get(
        "/v1/observations",
        params=params("population_2022", geography_id=["01001"]),
        headers={"X-Request-ID": "context-timing-288"},
    )
    assert response.status_code == 200
    events = [r.context for r in caplog.records if r.message == "api_request_completed"]
    event = events[-1]
    assert event["request_id"] == "context-timing-288"
    assert event["context_timing_ms"]["service"] >= 0
    assert event["context_operation_counts"]["service"] == 1
    assert "context_before_service_ms" in event
    assert "fixture" not in json.dumps(event).lower()
    assert timing.CURRENT.get() is None
