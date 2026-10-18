"""OpenAPI 3.1 nullable type normalization and SDK compatibility regressions."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openapi_schema_validator import OAS31Validator

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.openapi_nullable import normalize_nullable_types

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = json.loads((ROOT / "tests/fixtures/openapi-nullable-type-snapshot.json").read_text())


def _resolve_pointer(document: dict[str, Any], pointer: str) -> Any:
    value: Any = document
    for segment in pointer.strip("/").split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        value = value[int(segment)] if isinstance(value, list) else value[segment]
    return value


def _nullable_any_of_schemas(document: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("anyOf"), list) and any(
                branch == {"type": "null"} for branch in value["anyOf"]
            ):
                found.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return found


def test_nullable_schema_snapshots_preserve_constraints_and_union_types() -> None:
    app = create_app(settings=ApiSettings())
    documents = {
        "openapi.json": app.openapi(),
        "first-party-openapi.json": app.first_party_openapi(),
    }
    for filename, pointers in SNAPSHOT.items():
        for pointer, expected in pointers.items():
            assert _resolve_pointer(documents[filename], pointer) == expected


def test_nullable_normalization_is_idempotent_and_preserves_unflattenable_refs() -> None:
    app = create_app(settings=ApiSettings())
    for document in (app.openapi(), app.first_party_openapi()):
        generated = copy.deepcopy(document)
        assert normalize_nullable_types(generated) == 0
        assert generated == document
        for schema in _nullable_any_of_schemas(document):
            assert any(isinstance(branch, dict) and "$ref" in branch for branch in schema["anyOf"])


def test_schema_shaped_literal_data_is_opaque_but_nested_schemas_are_normalized() -> None:
    literal = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    before = {
        "type": "object",
        "enum": [literal],
        "default": {"schema": literal},
        "examples": [{"anyOf": [{"type": "string"}, {"type": "null"}]}],
        "properties": {
            "actual": {"anyOf": [{"type": "string", "maxLength": 5}, {"type": "null"}]}
        },
        "items": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    }
    after = copy.deepcopy(before)
    assert normalize_nullable_types(after) == 2
    assert after["enum"] == [literal]
    assert after["default"] == {"schema": literal}
    assert after["examples"] == [{"anyOf": [{"type": "string"}, {"type": "null"}]}]
    assert after["properties"]["actual"]["type"] == ["string", "null"]
    assert after["items"]["type"] == ["integer", "null"]
    instance = literal
    assert OAS31Validator(before).is_valid(instance)
    assert OAS31Validator(after).is_valid(instance)


def test_openapi_examples_are_opaque_and_component_schemas_are_normalized() -> None:
    literal = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    document = {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                "Value": {"anyOf": [{"type": "integer"}, {"type": "null"}]}
            }
        },
        "x-example": {"schema": literal},
        "paths": {
            "/x": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"enum": [literal], "type": "object"},
                                    "examples": {"sample": {"value": literal}},
                                }
                            }
                        }
                    }
                }
            }
        },
    }
    assert normalize_nullable_types(document) == 1
    assert document["x-example"]["schema"] == literal
    assert document["components"]["schemas"]["Value"]["type"] == ["integer", "null"]
    media = document["paths"]["/x"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]
    assert media["schema"]["enum"] == [literal]
    assert media["examples"]["sample"]["value"] == literal


@pytest.mark.parametrize(
    ("before", "instances"),
    [
        (
            {
                "anyOf": [
                    {"type": "integer", "minimum": 1900, "maximum": 2100},
                    {"type": "null"},
                ]
            },
            [None, 1899, 1900, 2026, 2100, 2101, "2026"],
        ),
        (
            {
                "title": "Publication date",
                "anyOf": [
                    {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                    {"type": "null"},
                ],
            },
            [None, "2026-10-18", "18-10-2026", 20261018],
        ),
        (
            {
                "anyOf": [
                    {"type": "string", "enum": ["AVAILABLE", "UNAVAILABLE"]},
                    {"type": "null"},
                ]
            },
            [None, "AVAILABLE", "UNAVAILABLE", "MISSING", 1],
        ),
        (
            {"anyOf": [{"type": "string", "const": "NO_PUBLISHED_RESULTS"}, {"type": "null"}]},
            [None, "NO_PUBLISHED_RESULTS", "AVAILABLE", 1],
        ),
        (
            {
                "anyOf": [
                    {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                    {"type": "null"},
                ]
            },
            [None, [], ["a"], ["a", "b"], ["a", "b", "c"], [1]],
        ),
        (
            {"anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}]},
            [None, 0, "zero", 0.5, True],
        ),
    ],
)
def test_type_array_normalization_is_json_schema_equivalent(
    before: dict[str, Any], instances: list[Any]
) -> None:
    after = copy.deepcopy(before)
    assert normalize_nullable_types(after) == 1
    old_validator = OAS31Validator(before)
    new_validator = OAS31Validator(after)
    for instance in instances:
        assert old_validator.is_valid(instance) == new_validator.is_valid(instance)


def test_generated_sdk_shape_keeps_existing_models_and_nullable_refs() -> None:
    schema = TestClient(create_app(settings=ApiSettings())).get("/openapi.json").json()
    response = schema["paths"]["/v1/observations"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert response["anyOf"] == [
        {"$ref": "#/components/schemas/CollectionEnvelope_CountyContextObservation_"},
        {"$ref": "#/components/schemas/CollectionEnvelope_Observation_"},
    ]
    assert schema["components"]["schemas"]["CollectionMeta"]["properties"][
        "availability"
    ] == {
        "title": "Availability",
        "description": (
            "Observation selection availability, not biological presence. Omitted on other "
            "collections. An exhausted continuation remains AVAILABLE."
        ),
        "enum": ["AVAILABLE", "UNAVAILABLE", None],
        "type": ["string", "null"],
    }
    # A nullable model reference stays anyOf instead of becoming an invalid
    # or narrowing `type` sibling beside `$ref`.
    assert schema["components"]["schemas"]["Geography"]["properties"]["parent"] == {
        "anyOf": [
            {"$ref": "#/components/schemas/GeographyIdentity"},
            {"type": "null"},
        ]
    }
