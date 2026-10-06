"""Preserve existing internal Web client/validator inputs alongside public docs."""

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_openapi import public_projection

ROOT = Path(__file__).resolve().parents[1]
FLOOR = json.loads((ROOT / "tests/fixtures/first-party-internal-contract.json").read_text())
IDS = json.loads((ROOT / "tests/fixtures/first-party-operation-ids.json").read_text())


def test_existing_internal_operations_and_schemas_remain_in_first_party_export() -> None:
    schema = create_app(settings=ApiSettings()).first_party_openapi()
    committed = json.loads((ROOT / "first-party-openapi.json").read_text())
    for contract in (schema, committed):
        actual_ids = {
            path: {method: operation["operationId"] for method, operation in operations.items()}
            for path, operations in contract["paths"].items()
        }
        assert actual_ids == IDS
        assert sum(len(operations) for operations in actual_ids.values()) == 32
        for path, operations in FLOOR["paths"].items():
            assert contract["paths"][path] == operations, path
        for name, model in FLOOR["schemas"].items():
            assert contract["components"]["schemas"][name] == model, name


def test_independent_caches_and_docs_never_change_route_visibility() -> None:
    for public_first in (True, False):
        app = create_app(settings=ApiSettings())
        flags = [getattr(route, "include_in_schema", None) for route in app.routes]
        if public_first:
            public, complete = app.openapi(), app.first_party_openapi()
        else:
            complete, public = app.first_party_openapi(), app.openapi()
        assert public is app.openapi()
        assert complete is app.first_party_openapi()
        assert public is not complete
        assert "/v1/knowledge-graph/chat" not in public["paths"]
        assert "/v1/knowledge-graph/chat" in complete["paths"]
        assert "/v1/assistant/structured" not in public["paths"]
        assert "/v1/assistant/structured" in complete["paths"]
        assert "/v1/assistant/mixed" not in public["paths"]
        assert "/v1/assistant/mixed" in complete["paths"]
        assert flags == [getattr(route, "include_in_schema", None) for route in app.routes]
        client = TestClient(app)
        for path in ("/docs", "/redoc"):
            assert "/openapi.json" in client.get(path).text


def test_projection_does_not_mutate_first_party_or_expose_new_internal_routes() -> None:
    complete = create_app(settings=ApiSettings()).first_party_openapi()
    complete["paths"]["/v1/internal/future-tool"] = {
        "get": {
            "operationId": "future_tool",
            "responses": {
                "200": {
                    "description": "Internal",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/FutureInternalTool"},
                        }
                    },
                }
            },
        },
    }
    complete["components"]["schemas"]["FutureInternalTool"] = {"type": "object"}
    before = deepcopy(complete)
    public = public_projection(complete)
    assert complete == before
    assert "/v1/internal/future-tool" not in public["paths"]
    assert "FutureInternalTool" not in public["components"]["schemas"]
    assert "BriefingArtifact" not in public["components"]["schemas"]
    assert "KnowledgeChatResponse" in complete["components"]["schemas"]


def test_public_http_and_first_party_build_match_exports_and_resolve_references() -> None:
    app = create_app(settings=ApiSettings())
    client = TestClient(app)
    assert client.get("/public/openapi.json").status_code == 404
    assert client.get("/first-party-openapi.json").status_code == 404
    for schema, filename in (
        (client.get("/openapi.json").json(), "openapi.json"),
        (app.first_party_openapi(), "first-party-openapi.json"),
    ):
        assert schema == json.loads((ROOT / filename).read_text())

        def visit(value: object, document: dict) -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item, document)
            elif isinstance(value, dict):
                ref = value.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/"):
                    target = document
                    for segment in ref[2:].split("/"):
                        target = target[segment]
                for item in value.values():
                    visit(item, document)

        visit(schema, schema)
