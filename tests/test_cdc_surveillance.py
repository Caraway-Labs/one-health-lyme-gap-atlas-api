"""Offline fixtures prove API binding, never production acceptance."""

import copy

import pytest
from fastapi.testclient import TestClient
from test_public_observations import FixtureRepository
from test_surveillance_delivery import admitted_evidence

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.cdc_surveillance import (
    BUNDLE,
    IDENTITIES,
    RELEASE,
    CDCObservationAdapter,
    _read,
)
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_contract import ObservationQuery
from lyme_gap_atlas_api.public_observations import ObservationService
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError


def settings(**changes):
    return ApiSettings(
        snowflake_auth_method="key_pair",
        snowflake_user="OH_LYME_API_SVC",
        snowflake_role="OH_LYME_PROD_READ",
        snowflake_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        snowflake_warehouse="COMPUTE_WH",
        **changes,
    )


def envelope(measure, county):
    result = admitted_evidence()
    s = result["semantic"]
    dataset, version, revision, _ = IDENTITIES[measure]
    s["measure"].update(
        id=measure,
        metadata_revision=2,
        metadata_revision_id=revision,
        allowed_value_states=["OBSERVED", "NO_RECORDS"],
    )
    s["release"] = {"id": RELEASE, "bundle_sha256": BUNDLE}
    s["lineage"].update(
        release_id=RELEASE,
        measure_id=measure,
        dataset_id=dataset,
        source_version_id=version,
        metadata_revision_id=revision,
    )
    s["provenance"]["source_version_id"]["value"] = version
    s["provenance"]["dataset_id"]["value"] = dataset
    s["observation"]["geography"]["county_fips"] = county
    if measure == "burgdorferi_status":
        s["observation"]["value"] = "No records" if county == "12047" else "Present"
    if county == "12047":
        s["observation"]["value_state"] = "NO_RECORDS"
        result["surveillance_evidence"].update(
            value_state="NO_RECORDS", normalized_state="unknown", state="unknown"
        )
    else:
        result["surveillance_evidence"]["normalized_state"] = "detected"
    return result


def client(producer=envelope, config=None):
    class Repository(FixtureRepository):
        def current_release(self):
            return RELEASE

        def measure_exists(self, measure_id, release):
            raise AssertionError("CDC must use the approved producer, not public view membership")

    api = create_app(settings=config or settings(), observation_repository=Repository())
    api.state.observation_service = ObservationService(
        Repository(), cdc_adapter=CDCObservationAdapter(config or settings(), producer)
    )
    return TestClient(api)


def params(measure="scapularis_status", county="13081", **changes):
    return dict(
        measure_id=measure,
        geography_type="county",
        geography_id=county,
        start_date="2025-12-31",
        end_date="2025-12-31",
        **changes,
    )


@pytest.mark.parametrize(
    "measure,county,value,state,normalized",
    [
        ("scapularis_status", "13081", "Reported", "OBSERVED", "detected"),
        ("burgdorferi_status", "19135", "Present", "OBSERVED", "detected"),
        ("burgdorferi_status", "12047", "No records", "NO_RECORDS", "unknown"),
    ],
)
def test_three_accepted_cases(measure, county, value, state, normalized):
    response = client().get("/v1/observations", params=params(measure, county))
    assert response.status_code == 200, response.text
    item = response.json()["data"][0]
    assert (item["value"], item["value_state"]) == (value, state)
    assert item["surveillance_context"]["evidence"]["normalized_state"] == normalized
    assert item["release_id"] == RELEASE
    assert item["metadata_revision_id"] == IDENTITIES[measure][2]
    assert item["limitations"] and item["surveillance_context"]["evidence"]["limitations"]
    for private in (
        "bundle_sha256",
        "source_version_id",
        "authority_reference",
        "record_refs",
        "lineage_id",
    ):
        assert private not in response.text


@pytest.mark.parametrize(
    "failure", ["release", "bundle", "metadata", "source", "county", "private", "value", "tier"]
)
def test_mismatched_producer_is_unavailable(failure):
    result = envelope("scapularis_status", "13081")
    if failure in {"release", "bundle"}:
        result["semantic"]["release"]["id" if failure == "release" else "bundle_sha256"] = "a" * 64
    elif failure == "metadata":
        result["semantic"]["measure"]["metadata_revision_id"] = "unreviewed"
    elif failure == "source":
        result["semantic"]["provenance"]["source_version_id"]["value"] = "wrong"
    elif failure == "county":
        result["semantic"]["observation"]["geography"]["county_fips"] = "08013"
    elif failure == "private":
        result["semantic"]["lineage"]["private_proof"] = "not public"
    elif failure == "value":
        result["surveillance_evidence"]["value_state"] = "NO_RECORDS"
    else:
        result["semantic"]["evidence_tier"] = "SYNTHETIC_FIXTURE"
    original = copy.deepcopy(result)
    response = client(lambda *_: result).get("/v1/observations", params=params())
    assert response.status_code == 503
    assert result == original
    assert "not public" not in response.text


def test_unavailable_producer_and_incorrect_identity():
    def denied(*args):
        raise PermissionError("private database detail")

    response = client(denied).get("/v1/observations", params=params())
    assert response.status_code == 503 and "private database detail" not in response.text
    bad = settings().model_copy(update={"snowflake_auth_method": "pat"})
    assert client(config=bad).get("/v1/observations", params=params()).status_code == 503


def test_unapproved_membership_and_annual_coercion_rejected():
    assert client().get("/v1/observations", params=params(county="08013")).status_code == 503
    query = params()
    query.pop("start_date")
    query.pop("end_date")
    query["year"] = 2025
    assert client().get("/v1/observations", params=query).status_code == 503


def test_pagination_binds_to_release_and_filters():
    query = params("burgdorferi_status", ["12047", "19135"], page_size=1)
    api = client()
    first = api.get("/v1/observations", params=query).json()
    query["page_token"] = first["meta"]["next_page_token"]
    second = api.get("/v1/observations", params=query).json()
    assert first["data"][0]["geography"]["geography_id"] == "12047"
    assert second["data"][0]["geography"]["geography_id"] == "19135"
    assert second["meta"]["next_page_token"] is None


def test_enabled_app_binds_existing_service():
    api = create_app(settings=settings(cdc_surveillance_enabled=True))
    assert isinstance(api.state.observation_service.cdc_adapter, CDCObservationAdapter)
    assert create_app(settings=settings()).state.observation_service.cdc_adapter is None


@pytest.mark.parametrize("county", ["12047", "19135"])
def test_packaged_producer_preserves_configured_timeout_on_identity_and_authority(
    monkeypatch, county
):
    from lyme_gap_atlas_data import cdc_consumer_runtime
    from test_cdc_reader_probe import Connection, ProducerCursor

    cursor = ProducerCursor()
    monkeypatch.setattr("lyme_gap_atlas_api.cdc_surveillance.connect", lambda _: Connection(cursor))
    with pytest.raises(AtlasDataUnavailableError):
        _read("burgdorferi_status", county, settings=settings(public_query_timeout_seconds=5))
    assert cursor.statements[0] == (
        "ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS=5",
        {"timeout": 5},
    )
    assert cursor.statements[1] == ("USE SECONDARY ROLES NONE", {"timeout": 5})
    assert [statement[0] for statement in cursor.statements[2:]] == [
        cdc_consumer_runtime.IDENTITY_SQL,
        cdc_consumer_runtime.AUTHORITY_SQL,
    ]
    assert all(kwargs == {"timeout": 5} for _, kwargs in cursor.statements)


@pytest.mark.parametrize("county", ["12047", "19135"])
def test_actual_producer_transport_timeout_becomes_sanitized_http_unavailable(monkeypatch, county):
    from test_cdc_reader_probe import Connection, ProducerCursor

    config = settings(public_query_timeout_seconds=5)
    cursor = ProducerCursor(authority_timeout=True)
    monkeypatch.setattr("lyme_gap_atlas_api.cdc_surveillance.connect", lambda _: Connection(cursor))
    response = client(None, config).get(
        "/v1/observations", params=params("burgdorferi_status", county)
    )
    assert response.status_code == 503
    assert "surveillance_context" not in response.text and "private" not in response.text
    assert all(kwargs == {"timeout": 5} for _, kwargs in cursor.statements)


def test_wrong_release_fails_before_producer():
    adapter = CDCObservationAdapter(settings(), lambda *_: pytest.fail("must not query"))
    with pytest.raises(AtlasDataUnavailableError):
        adapter.search(ObservationQuery(**params(county=["13081"])), "wrong", 0)
