"""Error responses in the public specification say what to do and show real bodies."""

import re
from typing import Any

from fastapi.testclient import TestClient
from test_public_metadata import MetadataFixture
from test_public_observations import client as observation_client
from test_public_observations import params as observation_params

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings

ALLOWED_CODES = {
    "INVALID_REQUEST",
    "UNSUPPORTED_FILTER",
    "UNSUPPORTED_STRATIFICATION",
    "QUERY_TOO_BROAD",
    "RESOURCE_NOT_FOUND",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
PROBLEM = "application/problem+json"


def _problem_responses(schema: dict[str, Any]) -> list[tuple[str, str, str, dict[str, Any]]]:
    found = []
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            for status, response in operation["responses"].items():
                if status.startswith("2") or status == "304":
                    continue
                found.append((path, method, status, response))
    return found


def _spec() -> dict[str, Any]:
    return create_app(settings=ApiSettings()).openapi()


def _example(schema: dict[str, Any], path: str, status: str, name: str) -> dict[str, Any]:
    responses = schema["paths"][path]["get"]["responses"]
    value = responses[status]["content"][PROBLEM]["examples"][name]["value"]
    assert isinstance(value, dict)
    return value


def _assert_matches(example: dict[str, Any], real: dict[str, Any]) -> None:
    """The example equals the body the API returns, apart from request-specific fields."""
    ignored = {"request_id", "instance"}
    expected = {k: v for k, v in example.items() if k not in ignored}
    actual = {k: v for k, v in real.items() if k not in ignored}
    expected.setdefault("errors", None)
    actual.setdefault("errors", None)
    if expected["errors"] is not None:
        actual["errors"] = [
            {k: v for k, v in entry.items() if k in expected["errors"][0]}
            for entry in actual["errors"]
        ]
    assert actual == expected


def test_every_error_response_has_a_sentence_description_and_a_known_code_example() -> None:
    for path, method, status, response in _problem_responses(_spec()):
        description = response["description"]
        label = f"{method} {path} {status}"
        assert re.search(r"[A-Za-z`].*\.$", description), label
        assert len(description.split()) >= 8, label
        assert not re.search(r"#\d|API #|ADR", description), label
        examples = response["content"][PROBLEM]["examples"]
        assert examples, label
        for example in examples.values():
            assert example["summary"] and example["description"].endswith("."), label
            value = example["value"]
            assert value["request_id"] != "example-request-id", label
            assert value["instance"] == path, label
            assert value["status"] == int(status), label
            if "code" in value:
                assert value["code"] in ALLOWED_CODES, label
            else:
                assert status in {"404", "413", "422", "429"}, label


def test_retry_after_is_documented_on_every_429_and_503() -> None:
    for path, method, status, response in _problem_responses(_spec()):
        if status in {"429", "503"}:
            assert "Retry-After" in response["headers"], f"{method} {path} {status}"
        if status == "429":
            assert "`Retry-After`" in response["description"]
        if status == "503":
            assert "`Content-Type`" in response["description"], f"{method} {path}"
            assert "application/problem+json" in response["description"]


def test_observation_400_shows_one_named_example_for_each_code() -> None:
    responses = _spec()["paths"]["/v1/observations"]["get"]["responses"]
    examples = responses["400"]["content"][PROBLEM]["examples"]
    assert {name: item["value"].get("code") for name, item in examples.items()} == {
        "invalid_request": "INVALID_REQUEST",
        "unsupported_filter": "UNSUPPORTED_FILTER",
        "unsupported_stratification": "UNSUPPORTED_STRATIFICATION",
        "query_too_broad": "QUERY_TOO_BROAD",
    }
    other = _spec()["paths"]["/v1/measures"]["get"]["responses"]["400"]
    assert set(other["content"][PROBLEM]["examples"]) == {"invalid_request", "unsupported_filter"}


def test_every_stable_code_has_an_example_in_the_public_spec() -> None:
    seen = {
        value.get("code")
        for _, _, _, response in _problem_responses(_spec())
        for example in response["content"][PROBLEM]["examples"].values()
        for value in [example["value"]]
    }
    assert seen >= ALLOWED_CODES


def test_problem_details_fields_are_explained() -> None:
    properties = _spec()["components"]["schemas"]["ProblemDetails"]["properties"]
    assert set(properties) == {
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "request_id",
        "code",
        "errors",
    }
    assert all(len(item["description"].split()) >= 6 for item in properties.values())
    assert "support" in properties["request_id"]["description"]


def test_400_examples_equal_the_bodies_the_api_returns() -> None:
    schema = _spec()
    observations = observation_client()
    missing = observation_params()
    del missing["measure_id"]
    real = observations.get("/v1/observations", params=missing)
    _assert_matches(_example(schema, "/v1/observations", "400", "invalid_request"), real.json())
    assert real.headers["content-type"] == PROBLEM and real.status_code == 400

    real = TestClient(create_app(settings=ApiSettings())).get("/v1/measures", params={"bogus": "1"})
    _assert_matches(_example(schema, "/v1/measures", "400", "unsupported_filter"), real.json())

    real = observations.get(
        "/v1/observations", params=observation_params(stratification="age:adult")
    )
    _assert_matches(
        _example(schema, "/v1/observations", "400", "unsupported_stratification"), real.json()
    )

    broad = TestClient(create_app(settings=ApiSettings(public_query_result_ceiling=10))).get(
        "/v1/observations",
        params={
            "measure_id": "m",
            "geography_type": "county",
            "geography_id": "08001",
            "start_date": "2020-01-01",
            "end_date": "2031-01-01",
        },
    )
    assert broad.status_code == 400
    _assert_matches(_example(schema, "/v1/observations", "400", "query_too_broad"), broad.json())


def test_404_examples_equal_the_bodies_the_api_returns() -> None:
    schema = _spec()
    real = observation_client().get(
        "/v1/observations", params=observation_params(measure_id="unknown")
    )
    assert real.status_code == 404
    _assert_matches(_example(schema, "/v1/observations", "404", "resource_not_found"), real.json())

    metadata = TestClient(
        create_app(
            settings=ApiSettings(rate_limit_per_minute=100), metadata_repository=MetadataFixture()
        )
    )
    for path, template in (
        ("/v1/measures/missing", "/v1/measures/{measure_id}"),
        ("/v1/indicators/missing", "/v1/indicators/{indicator_id}"),
    ):
        real = metadata.get(path)
        assert real.status_code == 404
        _assert_matches(_example(schema, template, "404", "resource_not_found"), real.json())


def test_429_413_414_and_503_examples_equal_the_bodies_the_api_returns() -> None:
    schema = _spec()
    limited_app = create_app(
        settings=ApiSettings(rate_limit_per_minute=100, public_rate_limit_per_minute=1),
        metadata_repository=MetadataFixture(),
    )
    limited = TestClient(limited_app)
    limited.get("/v1/indicators")
    real = limited.get("/v1/indicators")
    assert real.status_code == 429 and "Retry-After" in real.headers
    _assert_matches(_example(schema, "/v1/indicators", "429", "rate_limited"), real.json())

    bounded = TestClient(
        create_app(
            settings=ApiSettings(rate_limit_per_minute=100, public_max_query_bytes=512),
            metadata_repository=MetadataFixture(),
        )
    )
    real = bounded.request("GET", "/v1/indicators", content=b"x")
    assert real.status_code == 413
    _assert_matches(_example(schema, "/v1/indicators", "413", "get_body_unsupported"), real.json())
    real = bounded.get("/v1/indicators", params={"q": "x" * 512})
    assert real.status_code == 414
    _assert_matches(_example(schema, "/v1/indicators", "414", "query_string_too_long"), real.json())

    unavailable = TestClient(create_app(settings=ApiSettings()), raise_server_exceptions=False)
    real = unavailable.get("/v1/atlas/metadata")
    assert real.status_code == 503 and real.headers["content-type"] == PROBLEM
    _assert_matches(
        _example(schema, "/v1/atlas/metadata", "503", "service_unavailable"), real.json()
    )


def test_legacy_validation_and_rate_limit_examples_describe_the_real_body() -> None:
    schema = _spec()
    api = TestClient(create_app(settings=ApiSettings(rate_limit_per_minute=10)))
    real = api.get("/v1/counties/abc")
    assert real.status_code == 422 and "code" not in real.json()
    _assert_matches(
        _example(schema, "/v1/counties/{fips}", "422", "validation_failed"), real.json()
    )
    assert (
        "no `code`"
        in schema["paths"]["/v1/counties/{fips}"]["get"]["responses"]["422"]["description"]
    )

    for _ in range(10):
        api.get("/v1/atlas/metadata")
    real = api.get("/v1/atlas/metadata")
    assert real.status_code == 429 and "Retry-After" in real.headers
    _assert_matches(_example(schema, "/v1/atlas/metadata", "429", "rate_limited"), real.json())
