"""Public documentation boundary and stable consumer metadata."""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.models import ProblemDetails
from lyme_gap_atlas_api.public_contract import CollectionEnvelope, Observation

ROOT = Path(__file__).resolve().parents[1]
IDS = json.loads((ROOT / "tests/fixtures/public-operation-ids.json").read_text())


def test_public_metadata_and_stable_operation_ids() -> None:
    schema = create_app(settings=ApiSettings()).openapi()
    assert set(schema["paths"]) == set(IDS)
    assert schema["info"]["title"] == "One Health Lyme Gap Atlas API"
    assert schema["info"]["version"] == ApiSettings().app_version
    assert "provenance" in schema["info"]["summary"]
    assert schema["info"]["contact"]["url"] == "https://carawaylabs.com/"
    for term in ("anonymous", "RFC 9457", "Fumadocs", "10,000", "2023", "deprecation"):
        assert term in schema["info"]["description"]
    tags = {tag["name"] for tag in schema["tags"]}
    operations = []
    for path, expected in IDS.items():
        operation = schema["paths"][path]["get"]
        assert operation["operationId"] == expected
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", expected)
        assert operation["summary"] and operation["description"]
        assert set(operation["tags"]) <= tags
        assert "security" not in operation
        operations.append(expected)
    assert len(operations) == len(set(operations))
    assert "securitySchemes" not in schema["components"]
    models = schema["components"]["schemas"]
    for name in (
        "KnowledgeChatRequest",
        "KnowledgeChatResponse",
        "UserProfileResponse",
        "PrivacyRequestCreated",
        "FeedbackSubmissionRequest",
    ):
        assert name not in models


def test_internal_product_routes_remain_registered_and_callable() -> None:
    app = create_app(settings=ApiSettings(knowledge_chat_enabled=False))
    paths = {getattr(route, "path", "") for route in app.routes}
    assert {
        "/v1/me/profile",
        "/v1/knowledge-graph/chat",
        "/v1/feedback",
        "/v1/me/privacy-requests",
        "/health/live",
        "/health/ready",
    } <= paths
    client = TestClient(app)
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/v1/me/profile").status_code in {401, 503}
    response = client.post("/v1/knowledge-graph/chat", json={"message": "test question"})
    assert response.status_code == 503
    assert response.json()["status"] == "evidence_unavailable"


def test_examples_validate_and_error_matches_runtime() -> None:
    client = TestClient(create_app(settings=ApiSettings()))
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/observations"]["get"]
    example = operation["responses"]["200"]["content"]["application/json"]["examples"]
    for item in example.values():
        CollectionEnvelope[Observation].model_validate(item["value"])
    for status in ("400", "404", "413", "414", "429", "503"):
        media = operation["responses"][status]["content"]
        assert set(media) == {"application/problem+json"}
        error = media["application/problem+json"]["examples"]["illustrative"]["value"]
        assert ProblemDetails.model_validate(error).status == int(status)
    invalid = client.get("/v1/observations")
    assert invalid.status_code == 400
    assert invalid.headers["content-type"] == "application/problem+json"
    assert invalid.json()["code"] == "INVALID_REQUEST"
    models = schema["components"]["schemas"]
    for model, fields in {
        "Observation": (
            "value",
            "value_state",
            "methodology_version",
            "provenance_ref",
            "atlas_acquired_at",
        ),
        "CollectionMeta": ("response_at", "next_page_token"),
        "Source": ("source_id", "source_retrieved_at"),
    }.items():
        for field in fields:
            assert models[model]["properties"][field]["description"]
