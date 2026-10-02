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


def test_existing_internal_operations_and_schemas_remain_in_first_party_export() -> None:
    schema = create_app(settings=ApiSettings()).openapi()
    committed = json.loads((ROOT / "openapi.json").read_text())
    for contract in (schema, committed):
        for path, operations in FLOOR["paths"].items():
            assert contract["paths"][path] == operations, path
        for name, model in FLOOR["schemas"].items():
            assert contract["components"]["schemas"][name] == model, name


def test_projection_does_not_mutate_first_party_or_expose_new_internal_routes() -> None:
    complete = create_app(settings=ApiSettings()).openapi()
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


def test_both_served_exports_match_committed_contracts_and_resolve_references() -> None:
    client = TestClient(create_app(settings=ApiSettings()))
    for url, filename in (
        ("/openapi.json", "openapi.json"),
        ("/public/openapi.json", "public-openapi.json"),
    ):
        schema = client.get(url).json()
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
