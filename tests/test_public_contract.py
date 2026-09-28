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
    assert "/v1/me/profile" in paths
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
    assert "422" not in responses
    assert set(responses["400"]["content"]) == {"application/problem+json"}
    assert schema["components"]["schemas"]["ValueState"]["enum"] == [
        state.value for state in ValueState
    ]
    public_states = set(schema["components"]["schemas"]["ValueState"]["enum"])
    assert "NOT_APPLICABLE" not in public_states
    assert "INCOMPLETE" not in public_states
    observation_fields = schema["components"]["schemas"]["Observation"]["properties"]
    assert "applicability" not in observation_fields
    assert "completeness" not in observation_fields
    assert "value_state" in observation_fields
    assert "next_page_token" in schema["components"]["schemas"]["CollectionMeta"]["properties"]


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
    with pytest.raises(ValidationError):
        Observation.model_validate({**payload, "value_state": "OBSERVED"})
    with pytest.raises(ValidationError):
        Observation.model_validate({**payload, "value_state": "MISSING"})
    assert (
        Observation.model_validate({**payload, "value": None, "value_state": "SUPPRESSED"}).value
        is None
    )
    for orthogonal_dimension in ("NOT_APPLICABLE", "INCOMPLETE"):
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
            "start_date": "2023-01-01",
            "end_date": "2023-02-01",
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
