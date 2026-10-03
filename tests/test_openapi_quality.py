"""Negative regressions for the documentation/generator quality gate."""

import json
import runpy
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
TOOLS = runpy.run_path(str(ROOT / "scripts/openapi_quality.py"))
PUBLIC = json.loads((ROOT / "openapi.json").read_text())


@pytest.mark.parametrize("name", ["openapi.json", "first-party-openapi.json"])
def test_export_is_valid_openapi_and_examples_validate(name: str) -> None:
    TOOLS["validate_contract"](json.loads((ROOT / name).read_text()), public=name == "openapi.json")


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_id",
        "duplicate_id",
        "missing_metadata",
        "internal_path",
        "invalid_schema",
        "dangling_ref",
        "remote_ref",
        "invalid_example",
        "missing_error",
    ],
)
def test_gate_rejects_contract_regressions(mutation: str) -> None:
    schema = deepcopy(PUBLIC)
    operation = schema["paths"]["/v1/observations"]["get"]
    if mutation == "missing_id":
        operation.pop("operationId")
    elif mutation == "duplicate_id":
        operation["operationId"] = schema["paths"]["/v1/indicators"]["get"]["operationId"]
    elif mutation == "missing_metadata":
        operation.pop("summary")
    elif mutation == "internal_path":
        schema["paths"]["/v1/knowledge-graph/chat"] = {"get": deepcopy(operation)}
    elif mutation == "invalid_schema":
        schema["components"]["schemas"]["Observation"]["type"] = "invalid_type"
    elif mutation in {"dangling_ref", "remote_ref"}:
        operation["responses"]["200"]["content"]["application/json"]["schema"] = {
            "$ref": "#/components/schemas/Absent"
            if mutation == "dangling_ref"
            else "https://invalid.example/schema.json",
        }
    elif mutation == "invalid_example":
        operation["responses"]["200"]["content"]["application/json"]["examples"] = {
            "bad": {"value": {"data": "not an array"}},
        }
    elif mutation == "missing_error":
        operation["responses"] = {"200": operation["responses"]["200"]}
    with pytest.raises((ValueError, KeyError, ValidationError)):
        TOOLS["validate_contract"](schema, public=True)


def test_semantic_diff_ignores_format_and_key_order_but_exposes_changes() -> None:
    reformatted = json.loads(json.dumps(PUBLIC, sort_keys=True, separators=(",", ":")))
    assert TOOLS["semantic_diff"](PUBLIC, reformatted, "openapi.json") == ""
    reformatted["info"]["description"] += " Changed semantics."
    diff = TOOLS["semantic_diff"](PUBLIC, reformatted, "openapi.json")
    assert "Changed semantics." in diff
    assert diff == TOOLS["semantic_diff"](PUBLIC, reformatted, "openapi.json")
