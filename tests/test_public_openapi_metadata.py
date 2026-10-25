"""Public documentation boundary and stable consumer metadata."""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.models import ProblemDetails
from lyme_gap_atlas_api.public_contract import CollectionEnvelope, Observation
from lyme_gap_atlas_api.public_docs import PROBLEM_TYPE_BASE_URI

ROOT = Path(__file__).resolve().parents[1]
IDS = json.loads((ROOT / "tests/fixtures/public-operation-ids.json").read_text())


def test_public_metadata_and_stable_operation_ids() -> None:
    schema = create_app(settings=ApiSettings()).openapi()
    assert set(schema["paths"]) == set(IDS)
    assert schema["info"]["title"] == "One Health Lyme Gap Atlas API"
    assert schema["info"]["version"] == ApiSettings().app_version
    assert "provenance" in schema["info"]["summary"]
    assert schema["info"]["contact"]["url"] == "https://onehealthatlas.org/"
    for term in ("no API key", "RFC 9457", "10,000", "2023", "deprecation"):
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


def test_pending_geography_openapi_documents_typed_200_not_a_permanent_503() -> None:
    schema = create_app(settings=ApiSettings()).openapi()
    operation = schema["paths"]["/v1/geographies/{geography_type}/{geography_id}"]["get"]
    responses = operation["responses"]
    assert "503" not in responses
    assert "422" not in responses
    branches = responses["200"]["content"]["application/json"]["schema"]["anyOf"]
    assert [branch["$ref"] for branch in branches] == [
        "#/components/schemas/ResourceEnvelope_Geography_",
        "#/components/schemas/GeographyUnavailable",
    ]
    unavailable = schema["components"]["schemas"]["GeographyUnavailable"]
    assert set(unavailable["required"]) == {
        "availability",
        "reason",
        "data",
        "geography",
        "limitations",
    }
    assert unavailable["properties"]["reason"]["const"] == "CANONICAL_GEOGRAPHY_UNPUBLISHED"


def test_observation_variants_are_named_anyof_branches_for_sdk_generation() -> None:
    schema = create_app(settings=ApiSettings()).openapi()
    response_schema = schema["paths"]["/v1/observations"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert "oneOf" not in response_schema
    assert "discriminator" not in response_schema
    branches = response_schema["anyOf"]
    refs = [branch["$ref"] for branch in branches]
    assert refs == [
        "#/components/schemas/CollectionEnvelope_CountyContextObservation_",
        "#/components/schemas/CollectionEnvelope_Observation_",
    ]

    models = schema["components"]["schemas"]
    context = models["CollectionEnvelope_CountyContextObservation_"]
    standard = models["CollectionEnvelope_Observation_"]
    assert context["title"] == "County Context Observation Collection"
    assert standard["title"] == "Standard Observation Collection"
    assert context["properties"]["data"]["items"]["$ref"].endswith("/CountyContextObservation")
    assert standard["properties"]["data"]["items"]["$ref"].endswith("/Observation")
    context_fields = models["CountyContextObservation"]["properties"]
    standard_fields = models["Observation"]["properties"]
    assert "county_context" in context_fields and "county_context" not in standard_fields
    assert context_fields["period_start"]["type"] == ["string", "null"]
    assert standard_fields["period_start"]["format"] == "date"


def test_openapi_uses_canonical_domain_and_problem_type_uris_in_all_exports() -> None:
    app = create_app(settings=ApiSettings())
    for schema in (app.openapi(), app.first_party_openapi()):
        assert schema["servers"] == [
            {"url": "https://api.onehealthatlas.org", "description": "Production"}
        ]
        assert schema["externalDocs"]["url"] == "https://onehealthatlas.org/docs"
        for path_item in schema["paths"].values():
            for operation in path_item.values():
                for response in operation["responses"].values():
                    problem = response.get("content", {}).get("application/problem+json")
                    if problem is None:
                        continue
                    examples = problem.get("examples", {}).values()
                    values = [example.get("value", {}) for example in examples]
                    if "example" in problem:
                        values.append(problem["example"])
                    for value in values:
                        assert value["type"].startswith(f"{PROBLEM_TYPE_BASE_URI}/")
    assert "https://carawaylabs.com/problems/" not in str(app.first_party_openapi())


def test_problem_examples_match_operation_route_templates_and_documented_statuses() -> None:
    app = create_app(settings=ApiSettings())
    expected_statuses = {
        ("/v1/indicators", "get"): {"400", "404", "413", "414", "429", "503"},
        ("/v1/atlas/metadata", "get"): {"404", "422", "429", "503"},
        ("/v1/counties/{fips}/report.pdf", "get"): {"400", "404", "413", "422", "429", "503"},
    }
    for contract in (app.openapi(), app.first_party_openapi()):
        observed_statuses = set()
        for path, path_item in contract["paths"].items():
            for method, operation in path_item.items():
                if method.lower() not in {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "options",
                    "head",
                }:
                    continue
                for status, response in operation.get("responses", {}).items():
                    problem = response.get("content", {}).get("application/problem+json")
                    if problem is None:
                        continue
                    observed_statuses.add(status)
                    values = [
                        item.get("value", {}) for item in problem.get("examples", {}).values()
                    ]
                    if isinstance(problem.get("example"), dict):
                        values.append(problem["example"])
                    assert values
                    for value in values:
                        assert value["instance"] == path
                        assert value["status"] == int(status)
                        assert value["type"].startswith(f"{PROBLEM_TYPE_BASE_URI}/")
        assert {"400", "404", "413", "414", "422", "429", "503"} <= observed_statuses
        for (path, method), statuses in expected_statuses.items():
            responses = contract["paths"][path][method]["responses"]
            assert statuses <= {
                status
                for status, response in responses.items()
                if "application/problem+json" in response.get("content", {})
            }


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
    assert response.status_code == 200
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
    assert invalid.json()["type"] == f"{PROBLEM_TYPE_BASE_URI}/validation"
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


def _parameters(schema: dict) -> list[tuple[str, str, dict]]:
    return [
        (f"{method.upper()} {path}", parameter["name"], parameter)
        for path, item in schema["paths"].items()
        for method, operation in item.items()
        if isinstance(operation, dict)
        for parameter in operation.get("parameters", [])
    ]


def test_every_public_parameter_has_a_description() -> None:
    schema = create_app(settings=ApiSettings()).openapi()
    parameters = _parameters(schema)
    assert parameters
    missing = [
        f"{operation} {name}"
        for operation, name, parameter in parameters
        if not str(parameter.get("description", "")).strip()
    ]
    assert missing == []


def test_shared_parameters_use_one_description() -> None:
    schema = create_app(settings=ApiSettings()).openapi()
    for name in (
        "dataset_version",
        "ecological_share",
        "smooth_k",
        "missing_human_weakness",
        "low_incidence_breakpoint",
        "fips",
    ):
        descriptions = {
            parameter["description"]
            for _, parameter_name, parameter in _parameters(schema)
            if parameter_name == name
        }
        assert len(descriptions) == 1, name
    fips = next(p for _, n, p in _parameters(schema) if n == "fips")
    for phrase in ("five digits", "leading zeros", "string", "09110"):
        assert phrase in fips["description"]
    assert all(isinstance(example, str) for example in fips["schema"].get("examples", []))


def test_operation_descriptions_are_structured_and_short() -> None:
    schema = create_app(settings=ApiSettings()).openapi()
    for path, item in schema["paths"].items():
        description = item["get"]["description"]
        prose = "\n".join(
            line for line in description.splitlines() if not line.lstrip().startswith("|")
        )
        assert len(prose) <= 900, path
    observations = schema["paths"]["/v1/observations"]["get"]["description"]
    for heading in (
        "### What it returns",
        "### Required parameters",
        "### Time parameters by measure type",
        "### Value states",
        "### Empty versus unavailable results",
        "### Paging",
        "### Caching",
    ):
        assert heading in observations
    for term in ("`year`", "`start_date`", "`vintage_year`", "`UNSUPPORTED_FILTER`"):
        assert term in observations
    assert "`INVALID_REQUEST`" in observations
    assert "`NO_PUBLISHED_RESULTS`" in observations
