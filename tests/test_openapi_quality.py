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


# --- Documentation-quality rules: one passing and one failing fixture each ---------

RULES = TOOLS["load_rules"]()
OP = "/v1/observations"


def _spec() -> dict:
    return deepcopy(PUBLIC)


def _rules_of(violations: list) -> set[str]:
    return {violation.rule for violation in violations}


def test_published_spec_passes_documentation_rules() -> None:
    failures, _ = TOOLS["check_documentation_quality"](PUBLIC, RULES)
    assert [v.render() for v in failures] == []


def test_parameter_description_rule() -> None:
    assert TOOLS["check_parameter_descriptions"](PUBLIC) == []
    spec = _spec()
    spec["paths"][OP]["get"]["parameters"][0].pop("description", None)
    spec["paths"][OP]["get"]["parameters"][0].pop("$ref", None)
    found = TOOLS["check_parameter_descriptions"](spec)
    assert _rules_of(found) == {"parameter-description"}
    assert found[0].location == f"GET {OP}"


def test_schema_description_rule() -> None:
    assert TOOLS["check_schema_descriptions"](PUBLIC) == []
    spec = _spec()
    schema = spec["components"]["schemas"]["Observation"]
    schema.pop("description")
    prop = next(iter(schema["properties"]))
    schema["properties"][prop] = {"type": "string"}
    locations = {v.location for v in TOOLS["check_schema_descriptions"](spec)}
    assert locations == {"schema Observation", f"schema Observation.{prop}"}


def test_response_200_example_rule() -> None:
    assert TOOLS["check_response_200_examples"](PUBLIC) == []
    spec = _spec()
    for media in spec["paths"][OP]["get"]["responses"]["200"]["content"].values():
        media.pop("examples", None)
        media.pop("example", None)
    assert _rules_of(TOOLS["check_response_200_examples"](spec)) == {"response-200-example"}


def test_error_description_rule() -> None:
    assert TOOLS["check_error_responses"](PUBLIC) == []
    spec = _spec()
    spec["paths"][OP]["get"]["responses"]["400"]["description"] = ""
    assert _rules_of(TOOLS["check_error_responses"](spec)) == {"error-description"}


def test_error_example_rule() -> None:
    spec = _spec()
    for media in spec["paths"][OP]["get"]["responses"]["400"]["content"].values():
        media.pop("examples", None)
    assert _rules_of(TOOLS["check_error_responses"](spec)) == {"error-example"}
    # A body-less response such as 304 needs no example.
    spec["paths"][OP]["get"]["responses"]["304"] = {"description": "Not modified."}
    assert all("304" not in v.location for v in TOOLS["check_error_responses"](spec))


def test_description_length_rule() -> None:
    budgets = RULES["budgets"]
    assert TOOLS["check_description_budgets"](PUBLIC, budgets) == []
    spec = _spec()
    spec["paths"][OP]["get"]["description"] = "Short. " * (budgets["max_description_chars"] // 5)
    assert "description-length" in _rules_of(TOOLS["check_description_budgets"](spec, budgets))


def test_paragraph_length_rule() -> None:
    budgets = RULES["budgets"]
    spec = _spec()
    spec["paths"][OP]["get"]["description"] = "One. Two. Three. Four. Five."
    assert TOOLS["check_description_budgets"](spec, budgets) == []
    spec["paths"][OP]["get"]["description"] = "One. Two. Three. Four. Five. Six."
    assert _rules_of(TOOLS["check_description_budgets"](spec, budgets)) == {"paragraph-length"}
    # Tables and list items are not prose; abbreviations and code do not end sentences.
    table = "| a. | b. c. d. e. f. g. |"
    spec["paths"][OP]["get"]["description"] = f"Use e.g. `a. b. c.` here.\n\n{table}"
    assert TOOLS["check_description_budgets"](spec, budgets) == []


def test_summary_length_rule() -> None:
    budgets = RULES["budgets"]
    spec = _spec()
    spec["paths"][OP]["get"]["summary"] = "x" * budgets["max_summary_chars"]
    assert TOOLS["check_description_budgets"](spec, budgets) == []
    spec["paths"][OP]["get"]["summary"] += "x"
    assert _rules_of(TOOLS["check_description_budgets"](spec, budgets)) == {"summary-length"}


@pytest.mark.parametrize(
    "text",
    [
        "Typed 200 response",
        "This is persisted data",
        "See API #237 for details",
        "Tracked in #357",
        "Responses fail closed.",
        "Fumadocs reads this",
    ],
)
def test_banned_term_rule(text: str) -> None:
    banned = RULES["banned_terms"]
    spec = _spec()
    spec["paths"][OP]["get"]["responses"]["400"]["description"] = text
    found = TOOLS["check_banned_terms"](spec, banned)
    assert _rules_of(found) == {"banned-term"}
    assert "responses.400" in found[0].location


def test_banned_term_rule_allows_exact_values_in_code_font_and_example_data() -> None:
    banned = RULES["banned_terms"]
    spec = _spec()
    spec["paths"][OP]["get"]["responses"]["400"]["description"] = "Sends `no-store` here."
    assert TOOLS["check_banned_terms"](spec, banned) == []
    response = spec["paths"][OP]["get"]["responses"]["200"]["content"]["application/json"]
    next(iter(response["examples"].values()))["value"] = {"note": "persisted"}
    assert TOOLS["check_banned_terms"](spec, banned) == []


def test_operation_id_fixture_rule_enforces_current_ids() -> None:
    fixture = json.loads((ROOT / "tests/fixtures/public-operation-ids.json").read_text())
    assert TOOLS["check_operation_id_fixture"](PUBLIC, fixture, "openapi.json") == []
    spec = _spec()
    spec["paths"][OP]["get"]["operationId"] = "getWidget"
    found = TOOLS["check_operation_id_fixture"](spec, fixture, "openapi.json")
    assert _rules_of(found) == {"operation-id-fixture"}


def test_operation_id_naming_rule_is_enforced() -> None:
    ids = RULES["operation_ids"]
    assert ids["naming_convention"] == "enforce"
    assert TOOLS["check_operation_id_naming"](PUBLIC, ids["naming_pattern"]) == []
    spec = _spec()
    failures, advisories = TOOLS["check_documentation_quality"](spec, RULES)
    assert failures == [] and advisories == []
    spec["paths"][OP]["get"]["operationId"] = "observations_v1_observations_get"
    failures, advisories = TOOLS["check_documentation_quality"](spec, RULES)
    assert advisories == []
    assert _rules_of(failures) == {"operation-id-fixture", "operation-id-naming"}
    off = {**RULES, "operation_ids": {**ids, "naming_convention": "off"}}
    failures, advisories = TOOLS["check_documentation_quality"](spec, off)
    assert advisories == [] and _rules_of(failures) == {"operation-id-fixture"}
    first_party = json.loads((ROOT / "first-party-openapi.json").read_text())
    assert TOOLS["check_operation_id_naming"](first_party, ids["first_party_naming_pattern"]) == []


def test_exceptions_need_justification_and_must_still_match() -> None:
    violation = TOOLS["Violation"]("summary-length", f"GET {OP}", "too long")
    apply = TOOLS["apply_exceptions"]
    good = {"rule": "summary-length", "location": f"GET {OP}", "justification": "Because."}
    assert apply([violation], [good]) == []
    assert _rules_of(apply([violation], [{**good, "justification": " "}])) == {
        "summary-length",
        "exception",
    }
    assert _rules_of(apply([], [good])) == {"exception"}


def test_violation_output_names_operation_field_rule_and_style_guide() -> None:
    text = TOOLS["Violation"]("banned-term", "GET /v1/x", "banned term 'y'").render()
    assert "[banned-term]" in text and "GET /v1/x" in text
    assert "docs/api-writing-style.md#banned-and-replace-list" in text
