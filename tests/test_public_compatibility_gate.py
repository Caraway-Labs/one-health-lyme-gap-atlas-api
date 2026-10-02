"""Selective V1 compatibility floor from the committed OpenAPI contract.

The fixture records consumer-visible guarantees, not a second schema or a
whole-file snapshot. Changing the floor requires explicit compatibility review.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "public-openapi.json").read_text(encoding="utf-8"))
FLOOR = json.loads(
    (ROOT / "tests/fixtures/public-v1-compatibility.json").read_text(encoding="utf-8")
)


def _allows_null(schema: dict, schemas: dict) -> bool:
    if schema.get("type") == "null" or schema.get("nullable") is True:
        return True
    if "$ref" in schema:
        return _allows_null(schemas[schema["$ref"].split("/")[-1]], schemas)
    return any(
        _allows_null(branch, schemas)
        for key in ("anyOf", "oneOf")
        for branch in schema.get(key, [])
    )


def test_public_operations_and_problem_responses_remain_available() -> None:
    paths = CONTRACT["paths"]
    for path, floor in FLOOR["paths"].items():
        operation = paths[path][floor["method"]]
        response = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert response["$ref"].split("/")[-1] == floor["response"]
        required_params = {
            param["name"] for param in operation.get("parameters", []) if param.get("required")
        }
        assert required_params == set(floor["parameters"]), path
        for status in floor["errors"]:
            problem = operation["responses"][status]["content"]["application/problem+json"]
            assert problem["schema"]["$ref"] == "#/components/schemas/ProblemDetails"


def test_public_response_fields_remain_additively_compatible() -> None:
    schemas = CONTRACT["components"]["schemas"]
    for name, floor in FLOOR["models"].items():
        schema = schemas[name]
        assert set(floor["properties"]) <= set(schema["properties"]), name
        # Added optional fields are compatible; new required fields are not.
        assert set(schema.get("required", [])) == set(floor["required"]), name
    assert set(FLOOR["enums"]["ValueState"]) <= set(schemas["ValueState"]["enum"])
    for name, fields in FLOOR["nullable"].items():
        for field in fields:
            assert _allows_null(schemas[name]["properties"][field], schemas), (name, field)


def test_provenance_pagination_and_version_links_remain_explicit() -> None:
    schemas = CONTRACT["components"]["schemas"]
    assert {"next_page_token", "response_at"} <= set(schemas["CollectionMeta"]["properties"])
    assert {
        "source_id",
        "methodology_id",
        "provenance_ref",
        "evidence",
        "release_id",
        "semantic_version",
        "methodology_version",
    } <= set(schemas["Observation"]["required"])
    assert {"resource_type", "resource_id"} <= set(schemas["EvidenceReference"]["required"])
    assert {"semantic_version", "release_version"} <= set(schemas["Source"]["required"])
    assert {"semantic_version", "release_version"} <= set(schemas["Methodology"]["required"])
    assert {"status", "detail", "instance", "request_id"} <= set(
        schemas["ProblemDetails"]["required"]
    )
