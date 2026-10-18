"""Availability describes successful determinations, never swallowed failures."""

from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_cdc_surveillance import client as cdc_client
from test_cdc_surveillance import params as cdc_params
from test_public_observations import FixtureRepository, client, params
from test_tier1_priority import CurrentGeographyRepository

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_contract import (
    CollectionEnvelope,
    CollectionMeta,
    Observation,
)
from lyme_gap_atlas_api.public_observations import SnowflakeObservationRepository
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError


def test_successful_empty_selection_is_explicit_and_not_negative_evidence():
    response = client().get("/v1/observations", params=params(year=2024))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["data"] == []
    assert body["links"] == {"self": None}
    assert body["meta"]["availability"] == "UNAVAILABLE"
    assert body["meta"]["reason"] == "NO_PUBLISHED_RESULTS"
    assert body["meta"]["release_id"] == "release-1"
    assert body["meta"]["next_page_token"] is None
    assert "biological absence" in body["meta"]["limitations"][0]
    assert response.headers["x-request-id"]


def test_available_records_and_exhausted_continuation_are_distinct_from_empty_selection():
    api = client()
    first = api.get("/v1/observations", params=params(page_size=1)).json()
    assert first["meta"]["availability"] == "AVAILABLE"
    assert first["data"][0]["value_state"] == "MISSING"
    # A valid opaque continuation beyond the last row is page exhaustion only.
    from lyme_gap_atlas_api.public_contract import ObservationQuery
    from lyme_gap_atlas_api.public_observations import ObservationService

    query = ObservationQuery(**params(page_size=1))
    service = ObservationService(FixtureRepository())
    page = service.search(query)
    while page.meta.next_page_token:
        query.page_token = page.meta.next_page_token
        page = service.search(query)
    # Reuse a valid continuation after the underlying page becomes empty.
    repository = FixtureRepository()
    repository.query = Mock(return_value=[])  # type: ignore[method-assign]
    page = ObservationService(repository).search(query)
    assert page.data == []
    assert page.meta.availability == "AVAILABLE"
    assert page.meta.reason is None


@pytest.mark.parametrize("phase", ["current_release", "measure_exists", "query"])
@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError, AtlasDataUnavailableError])
def test_authority_and_dependency_failures_are_never_available(phase, failure):
    repository = FixtureRepository()
    setattr(repository, phase, Mock(side_effect=AtlasDataUnavailableError(failure.__name__)))
    response = client(repository).get("/v1/observations", params=params())
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["request_id"] == response.headers["x-request-id"]
    assert "availability" not in body
    assert failure.__name__ not in body["detail"]


@pytest.mark.parametrize("release", ["", " ", None])
def test_no_authoritative_release_cannot_establish_empty_availability(release):
    repository = FixtureRepository()
    repository.release = release
    response = client(repository).get("/v1/observations", params=params(year=2024))
    assert response.status_code == 503


@pytest.mark.parametrize(
    "second_release,status",
    [
        ("release-1", 200),
        ("release-2", 503),
        (AtlasDataUnavailableError("private release recheck failure"), 503),
    ],
)
def test_empty_selection_revalidates_current_release(second_release, status):
    repository = FixtureRepository()
    repository.current_release = Mock(side_effect=["release-1", second_release])
    response = client(repository).get("/v1/observations", params=params(year=2024))
    assert response.status_code == status
    assert repository.current_release.call_count == 2
    if status == 200:
        assert response.json()["meta"]["reason"] == "NO_PUBLISHED_RESULTS"
    else:
        assert response.json()["code"] == "SERVICE_UNAVAILABLE"
        assert "private" not in response.text


@pytest.mark.parametrize("measure", ["scapularis_status", "burgdorferi_status"])
def test_cdc_unclassified_state_remains_operational_error(measure):
    from lyme_gap_atlas_data.cdc_consumer_runtime import CDCConsumerUnavailable

    def producer(*_):
        # This exception has no consumer-safe unavailable discriminant.
        raise CDCConsumerUnavailable("UNKNOWN_SOURCE_COVERAGE: private authority")

    response = cdc_client(producer).get("/v1/observations", params=cdc_params(measure, ["02122"]))
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert "UNKNOWN_SOURCE_COVERAGE" not in response.text
    assert "private authority" not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": None},
        {"release_id": None},
        {"release_id": " "},
        {"limitations": []},
        {"next_page_token": "invented"},
    ],
)
def test_unavailable_metadata_requires_positive_selection_evidence(changes):
    values = {
        "availability": "UNAVAILABLE",
        "reason": "NO_PUBLISHED_RESULTS",
        "release_id": "checked-release",
        "limitations": ["Selection-only absence of published rows."],
        **changes,
    }
    with pytest.raises(ValidationError):
        CollectionMeta(**values)


def test_available_cannot_carry_unavailable_reason():
    with pytest.raises(ValidationError):
        CollectionMeta(availability="AVAILABLE", reason="NO_PUBLISHED_RESULTS")


def test_unavailable_collection_cannot_contain_invented_records():
    record = client().get("/v1/observations", params=params()).json()["data"][0]
    with pytest.raises(ValidationError):
        CollectionEnvelope[Observation].model_validate(
            {
                "data": [record],
                "meta": {
                    "availability": "UNAVAILABLE",
                    "reason": "NO_PUBLISHED_RESULTS",
                    "release_id": "checked-release",
                    "limitations": ["No data."],
                },
                "links": {},
            }
        )


@pytest.mark.parametrize("rows", [[], [(None,)], [("",)], [(" ",)], [(1,)], [("r",), ("r",)]])
def test_malformed_or_duplicate_release_authority_remains503(monkeypatch, rows):
    repository = SnowflakeObservationRepository(ApiSettings())
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchmany.return_value = rows
    monkeypatch.setattr(repository, "_cursor", lambda: cursor)
    response = client(repository).get("/v1/observations", params=params(year=2024))
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    cursor.fetchmany.assert_called_once_with(2)


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError])
@pytest.mark.parametrize("stage", ["connection", "sql"])
def test_actual_repository_wraps_connection_and_sql_failures(monkeypatch, failure, stage):
    repository = SnowflakeObservationRepository(ApiSettings())

    def fail(*_):
        raise failure("private connection/SQL detail")

    if stage == "connection":
        monkeypatch.setattr("lyme_gap_atlas_api.public_observations.connect", fail)
    else:
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.execute.side_effect = failure("private SQL timeout")
        monkeypatch.setattr(repository, "_cursor", lambda: cursor)
    response = client(repository).get("/v1/observations", params=params())
    assert response.status_code == 503
    assert "private" not in response.text


def geography_client(repository=None, **settings):
    return TestClient(
        create_app(
            settings=ApiSettings(**settings),
            repository=repository or CurrentGeographyRepository(["01001", "21007", "09110"]),
            observation_repository=FixtureRepository(),
        )
    )


@pytest.mark.parametrize("fips", ["01001", "21007", "09110"])
def test_checked_current_county_has_known_unpublished_capability(fips):
    response = geography_client().get(f"/v1/geographies/county/{fips}/availability")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["availability"] == "UNAVAILABLE"
    assert body["reason"] == "CANONICAL_GEOGRAPHY_UNPUBLISHED"
    assert body["release_id"] == "release-1"
    assert body["data"] is None and body["limitations"]
    assert "geometry" not in body
    assert response.headers["x-request-id"]


@pytest.mark.parametrize("fips,status", [("99999", 404), ("09001", 404), ("bad", 400)])
def test_missing_identity_is_not_a_known_unpublished_county(fips, status):
    response = geography_client().get(f"/v1/geographies/county/{fips}/availability")
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("kind", ["empty", "duplicate", "release", "blank_release", "malformed"])
def test_invalid_current_geography_authority_never_returns200(kind):
    repository = CurrentGeographyRepository(["01001"])
    snapshot = repository.load_snapshot()
    if kind == "empty":
        snapshot.counties.clear()
    elif kind == "duplicate":
        snapshot.counties.append(snapshot.counties[0])
    elif kind == "release":
        snapshot.counties[0] = snapshot.counties[0].model_copy(
            update={"release_id": "wrong-release"}
        )
    elif kind == "blank_release":
        snapshot.metadata.release_id = " "
    else:
        snapshot.counties[0] = snapshot.counties[0].model_copy(update={"fips": "bad"})
    repository.load_snapshot = Mock(return_value=snapshot)
    response = geography_client(repository).get("/v1/geographies/county/01001/availability")
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"


def test_geography_dependency_error_and_state_request_remain_errors():
    repository = CurrentGeographyRepository(["01001"])
    repository.load_snapshot = Mock(side_effect=AtlasDataUnavailableError("private auth failure"))
    response = geography_client(repository).get("/v1/geographies/county/01001/availability")
    assert response.status_code == 503 and "private" not in response.text
    response = geography_client().get("/v1/geographies/state/01/availability")
    assert response.status_code == 400 and response.json()["code"] == "UNSUPPORTED_FILTER"


@pytest.mark.parametrize("failure", [False, True])
def test_geography_fresh_release_mismatch_or_error_never_returns200(failure):
    app = create_app(
        settings=ApiSettings(),
        repository=CurrentGeographyRepository(["01001"]),
        observation_repository=FixtureRepository(),
    )
    repository = app.state.observation_service.repository
    repository.current_release = (
        Mock(side_effect=AtlasDataUnavailableError("private current release failure"))
        if failure
        else Mock(return_value="different-current-release")
    )
    response = TestClient(app).get("/v1/geographies/county/01001/availability")
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert "private" not in response.text


def test_geography_availability_retains_public_rate_protection():
    api = geography_client(public_rate_limit_per_minute=1)
    url = "/v1/geographies/county/01001/availability"
    assert api.get(url).status_code == 200
    response = api.get(url)
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    assert response.headers["retry-after"]


def test_other_collection_metadata_wire_shape_and_legacy_geography_are_unchanged():
    assert set(CollectionMeta().model_dump()) == {"response_at", "next_page_token"}
    response = geography_client().get("/v1/geographies/county/01001")
    assert response.status_code == 503
    assert response.json()["code"] == "CANONICAL_DATA_UNAVAILABLE"


def test_availability_examples_keep_required_nulls_in_both_schema_exports():
    from lyme_gap_atlas_api.geography_availability import CanonicalGeographyAvailability

    app = create_app(settings=ApiSettings())
    for schema in (app.openapi(), app.first_party_openapi()):
        operation = schema["paths"]["/v1/geographies/{geography_type}/{geography_id}/availability"][
            "get"
        ]
        example = operation["responses"]["200"]["content"]["application/json"]["examples"][
            "unpublished"
        ]["value"]
        assert "data" in example and example["data"] is None
        CanonicalGeographyAvailability.model_validate(example)
        observations = schema["paths"]["/v1/observations"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["examples"]
        assert observations["known_unavailable"]["value"]["meta"]["availability"] == "UNAVAILABLE"
        assert observations["available"]["value"]["meta"]["availability"] == "AVAILABLE"
        assert observations["empty"]["value"]["meta"]["next_page_token"] is None
