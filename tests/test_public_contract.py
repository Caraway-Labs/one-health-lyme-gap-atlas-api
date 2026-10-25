"""API #52 contract assertions; no warehouse publication is implied."""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_contract import (
    CollectionEnvelope,
    GeographyIdentity,
    Observation,
    ObservationQuery,
    PublicQueryError,
    ValueState,
)


def test_public_paths_and_generated_shapes() -> None:
    schema = create_app(settings=ApiSettings()).openapi()
    paths = schema["paths"]
    for path in (
        "/v1/indicators",
        "/v1/indicators/{indicator_id}",
        "/v1/measures",
        "/v1/measures/{measure_id}",
        "/v1/observations",
        "/v1/geographies/{geography_type}/{geography_id}",
        "/v1/sources",
        "/v1/sources/{source_id}",
        "/v1/methodologies/{methodology_id}",
    ):
        assert "get" in paths[path]
        assert "401" not in paths[path]["get"]["responses"]
        assert "400" in paths[path]["get"]["responses"]
    assert "/v1/me/profile" not in paths
    params = paths["/v1/observations"]["get"]["parameters"]
    names = {param["name"] for param in params}
    assert {
        "measure_id",
        "geography_type",
        "geography_id",
        "year",
        "start_date",
        "end_date",
        "page_size",
        "page_token",
    } <= names
    responses = paths["/v1/observations"]["get"]["responses"]
    assert "4XX" not in responses
    assert "422" not in responses
    assert set(responses["400"]["content"]) == {"application/problem+json"}
    for path in (
        "/v1/indicators/{indicator_id}",
        "/v1/measures/{measure_id}",
        "/v1/sources/{source_id}",
        "/v1/methodologies/{methodology_id}",
    ):
        assert "304" in paths[path]["get"]["responses"]
    assert schema["components"]["schemas"]["ValueState"]["enum"] == [
        "OBSERVED",
        "ZERO",
        "MISSING",
        "SUPPRESSED",
        "UNAVAILABLE",
        "NO_COUNTY_LINKED_RECORD",
        "NO_RECORDS",
    ]
    assert {state.value for state in ValueState} == {
        "OBSERVED",
        "ZERO",
        "MISSING",
        "SUPPRESSED",
        "UNAVAILABLE",
        "NO_COUNTY_LINKED_RECORD",
        "NO_RECORDS",
    }
    public_states = set(schema["components"]["schemas"]["ValueState"]["enum"])
    assert "NOT_APPLICABLE" not in public_states
    assert "INCOMPLETE" not in public_states
    observation_fields = schema["components"]["schemas"]["Observation"]["properties"]
    assert "applicability" not in observation_fields
    assert "completeness" not in observation_fields
    assert "value_state" in observation_fields
    assert "next_page_token" in schema["components"]["schemas"]["CollectionMeta"]["properties"]


def test_canonical_response_maps_snapshot_has_only_concrete_documented_statuses() -> None:
    app = create_app(settings=ApiSettings())
    expected = {
        # Typed 200 UNAVAILABLE replaces the permanent 503 (ADR 0021).
        "geography_v1_geographies__geography_type___geography_id__get": {
            "200",
            "400",
            "404",
            "413",
            "414",
            "429",
        },
        "canonicalGeographyAvailability": {"200", "400", "404", "413", "414", "429", "503"},
        "indicators_v1_indicators_get": {"200", "400", "404", "413", "414", "429", "503"},
        "indicator_v1_indicators__indicator_id__get": {
            "200",
            "304",
            "400",
            "404",
            "413",
            "414",
            "429",
            "503",
        },
        "measures_v1_measures_get": {"200", "400", "404", "413", "414", "429", "503"},
        "measure_v1_measures__measure_id__get": {
            "200",
            "304",
            "400",
            "404",
            "413",
            "414",
            "429",
            "503",
        },
        "methodology_v1_methodologies__methodology_id__get": {
            "200",
            "304",
            "400",
            "404",
            "413",
            "414",
            "429",
            "503",
        },
        "observations_v1_observations_get": {"200", "400", "404", "413", "414", "429", "503"},
        "sources_v1_sources_get": {"200", "400", "404", "413", "414", "429", "503"},
        "source_v1_sources__source_id__get": {
            "200",
            "304",
            "400",
            "404",
            "413",
            "414",
            "429",
            "503",
        },
        "listIntelligenceItems": {"200", "400", "404", "413", "414", "422", "429", "503"},
        "listIntelligenceSources": {"200", "400", "404", "413", "414", "422", "429", "503"},
        "listIntelligenceFeed": {"200", "400", "404", "413", "414", "422", "429", "503"},
        "getIntelligenceFeedEntry": {"200", "400", "404", "413", "414", "422", "429", "503"},
    }

    first_party_only = {
        "listIntelligenceItems",
        "listIntelligenceSources",
        "listIntelligenceFeed",
        "getIntelligenceFeedEntry",
    }
    for schema, included_ids in (
        (app.openapi(), set(expected) - first_party_only),
        (app.first_party_openapi(), set(expected)),
    ):
        operations = {
            operation["operationId"]: operation
            for path_item in schema["paths"].values()
            for operation in path_item.values()
            if isinstance(operation, dict) and "operationId" in operation
        }
        for operation in operations.values():
            assert "4XX" not in operation.get("responses", {})
        for operation_id, response_codes in expected.items():
            if operation_id not in included_ids:
                continue
            responses = operations[operation_id]["responses"]
            assert set(responses) == response_codes


def test_typed_geography_and_value_states() -> None:
    assert GeographyIdentity(geography_type="county", geography_id="08001").geography_id == "08001"
    assert GeographyIdentity(geography_type="state", geography_id="08").geography_id == "08"
    with pytest.raises(ValidationError):
        GeographyIdentity(geography_type="county", geography_id="Colorado")
    schema = Observation.model_json_schema()
    assert "value_state" in schema["properties"]
    assert "source_id" in schema["required"]
    assert "provenance_ref" in schema["required"]
    assert CollectionEnvelope[Observation].model_fields["meta"].is_required()
    payload = {
        "observation_id": "o1",
        "measure_id": "m1",
        "geography": {"geography_type": "county", "geography_id": "08001"},
        "period_start": "2023-01-01",
        "period_end": "2023-12-31",
        "temporal_grain": "year",
        "value": 0,
        "value_state": "ZERO",
        "unit": "count",
        "denominator": "NONE",
        "source_id": "cdc",
        "methodology_id": "method",
        "methodology_version": "1",
        "semantic_version": "1",
        "release_id": "release",
        "provenance_ref": "trace",
        "limitations": [],
        "evidence": {"resource_type": "observation", "resource_id": "o1"},
    }
    assert Observation.model_validate(payload).value_state == ValueState.ZERO
    # Synthetic contract evidence only: analytical provenance fits existing fields
    # without granting a public polygon surface or implying warehouse publication.
    analytical = Observation.model_validate(
        {
            **payload,
            "methodology_version": "atlas-county-analysis-geometry/1",
            "methodology": "atlas-grid-county-area-weight/1",
            "limitations": ["Frozen 2025 boundaries; no historical boundary reconstruction."],
        }
    ).model_dump()
    assert analytical["geography"] == payload["geography"]
    assert analytical["methodology_version"] == "atlas-county-analysis-geometry/1"
    assert analytical["provenance_ref"] == "trace"
    assert not {"geometry", "analysis_geometry", "display_geometry"} & analytical.keys()
    with pytest.raises(ValidationError):
        Observation.model_validate({**payload, "value_state": "OBSERVED"})
    with pytest.raises(ValidationError):
        Observation.model_validate({**payload, "value_state": "MISSING"})
    assert (
        Observation.model_validate({**payload, "value": None, "value_state": "SUPPRESSED"}).value
        is None
    )
    for orthogonal_dimension in (
        "NOT_APPLICABLE",
        "INCOMPLETE",
        "UNKNOWN",
        "NOT_REPORTED",
        "NOT_DEFENSIBLE",
        "NO_RECORDS",
        "NO_COUNTY_LINKED_RECORD",
    ):
        with pytest.raises(ValidationError):
            Observation.model_validate(
                {**payload, "value": None, "value_state": orthogonal_dimension}
            )


def test_applicability_and_quality_are_documented_as_additive_dimensions() -> None:
    contract = (Path(__file__).resolve().parents[1] / "docs/public-api-v1-contract.md").read_text(
        encoding="utf-8"
    )
    assert "independent semantic dimensions" in contract
    assert "optional, additive fields without reinterpreting" in contract
    assert "must not fabricate either dimension" in contract


def test_bounded_query_validation() -> None:
    config = ApiSettings()
    assert (config.public_page_size_default, config.public_page_size_max) == (100, 500)
    assert (
        config.public_rate_limit_per_minute,
        config.public_concurrent_requests_per_ip,
    ) == (60, 5)
    assert config.public_query_result_ceiling == 10_000
    assert config.public_query_timeout_seconds == 15
    base = {"measure_id": "m", "geography_type": "county", "geography_id": ["08001"]}
    ObservationQuery(**base, year=2023).validate_bounds(ceiling=10_000)
    with pytest.raises(PublicQueryError, match="year or a date range"):
        ObservationQuery(
            **base, year=2023, start_date=date(2023, 1, 1), end_date=date(2023, 1, 2)
        ).validate_bounds(ceiling=10_000)
    with pytest.raises(PublicQueryError) as exc:
        ObservationQuery(
            **base, start_date=date(2020, 1, 1), end_date=date(2025, 1, 1)
        ).validate_bounds(ceiling=100)
    assert exc.value.code == "QUERY_TOO_BROAD"


def test_canonical_problem_and_legacy_compatibility() -> None:
    client = TestClient(create_app(settings=ApiSettings(public_query_result_ceiling=10)))
    conflict = client.get(
        "/v1/observations",
        params={
            "measure_id": "m",
            "geography_type": "county",
            "geography_id": "08001",
            "year": 2023,
            "start_date": "2023-01-01",
            "end_date": "2023-01-02",
        },
    )
    assert conflict.status_code == 400
    assert conflict.headers["content-type"] == "application/problem+json"
    assert conflict.json()["code"] == "INVALID_REQUEST"
    broad = client.get(
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
    assert broad.json()["code"] == "QUERY_TOO_BROAD"
    unsupported = client.get(
        "/v1/observations",
        params={
            "measure_id": "m",
            "geography_type": "county",
            "geography_id": "08001",
            "year": 2023,
            "sql": "select *",
        },
    )
    assert unsupported.json()["code"] == "UNSUPPORTED_FILTER"
    assert client.get("/v1/geographies/county/08").json()["code"] == "INVALID_REQUEST"
    assert client.get("/v1/me/profile").status_code in {401, 503}
    assert client.get("/v1/atlas/scores?ecological_share=41").status_code == 422


@pytest.mark.parametrize(
    ("geography_type", "geography_id"),
    [("county", "01001"), ("county", "09001"), ("county", "09110"), ("state", "09")],
)
def test_valid_canonical_geographies_return_typed_unavailable_200(
    geography_type: str, geography_id: str
) -> None:
    """Pending identity delivery is a typed 200 for supported county and state keys."""
    client = TestClient(create_app(settings=ApiSettings()))
    response = client.get(f"/v1/geographies/{geography_type}/{geography_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    body = response.json()
    assert body["availability"] == "UNAVAILABLE"
    assert body["reason"] == "CANONICAL_GEOGRAPHY_UNPUBLISHED"
    assert body["data"] is None
    assert body["geography"] == {"geography_type": geography_type, "geography_id": geography_id}
    assert body["limitations"]
    assert "geometry" not in body


def test_every_public_schema_and_property_has_a_description() -> None:
    schemas = create_app(settings=ApiSettings()).openapi()["components"]["schemas"]
    undocumented = [name for name, schema in schemas.items() if not schema.get("description")]
    assert undocumented == []
    undocumented = [
        f"{name}.{prop}"
        for name, schema in schemas.items()
        for prop, value in (schema.get("properties") or {}).items()
        if not value.get("description")
    ]
    assert undocumented == []


def test_generic_envelopes_have_readable_titles() -> None:
    schemas = create_app(settings=ApiSettings()).openapi()["components"]["schemas"]
    for name in schemas:
        if name.startswith(("CollectionEnvelope_", "ResourceEnvelope_")):
            assert "_" not in schemas[name]["title"]
            assert "[" not in schemas[name]["title"]


def test_value_state_schema_describes_every_state() -> None:
    schemas = create_app(settings=ApiSettings()).openapi()["components"]["schemas"]
    description = schemas["ValueState"]["description"]
    for state in ValueState:
        assert f"`{state.value}`" in description
    assert "does not establish biological absence" in description
    assert "never turn `null` into zero" in description.lower()
