"""Offline fixtures prove API binding, never production acceptance."""

import copy
from unittest.mock import Mock

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
        metadata_revision=3,
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


def client(producer=envelope, config=None, provenance_repository=None):
    class Repository(FixtureRepository):
        def current_release(self):
            return RELEASE

        def measure_exists(self, measure_id, release):
            raise AssertionError("CDC must use the approved producer, not public view membership")

    api = create_app(
        settings=config or settings(),
        observation_repository=Repository(),
        provenance_repository=provenance_repository,
    )
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


@pytest.mark.parametrize("counties", ["08013", ["19135", "08013"]])
def test_unadmitted_counties_reach_producer_and_fail_closed(counties):
    repository = Mock()
    repository.current_release.return_value = RELEASE
    producer = Mock(side_effect=AssertionError("private lineage diagnostics"))
    api = create_app(settings=settings(), observation_repository=repository)
    api.state.observation_service = ObservationService(
        repository, cdc_adapter=CDCObservationAdapter(settings(), producer)
    )
    response = TestClient(api).get(
        "/v1/observations", params=params("burgdorferi_status", counties)
    )
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == (
        "The governed Atlas data service is temporarily unavailable."
    )
    assert response.json()["status"] == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert response.headers["x-request-id"] == response.json()["request_id"]
    repository.current_release.assert_called_once_with()
    repository.measure_exists.assert_not_called()
    repository.query.assert_not_called()
    producer.assert_called_once_with(
        "burgdorferi_status", sorted([counties] if isinstance(counties, str) else counties)[0]
    )
    assert "private" not in response.text
    assert "Snowflake" not in response.text
    assert "surveillance_context" not in response.text


def test_supported_cdc_still_checks_current_release_before_producer():
    repository = Mock()
    repository.current_release.return_value = "unapproved-release"
    producer = Mock()
    service = ObservationService(
        repository, cdc_adapter=CDCObservationAdapter(settings(), producer)
    )
    with pytest.raises(AtlasDataUnavailableError):
        service.search(ObservationQuery(**params(county=["13081"])))
    repository.current_release.assert_called_once_with()
    producer.assert_not_called()


def test_cdc_preflight_preserves_request_cancellation_checkpoint():
    repository, producer = Mock(), Mock()
    service = ObservationService(
        repository, cdc_adapter=CDCObservationAdapter(settings(), producer)
    )
    checkpoint = Mock(side_effect=TimeoutError("deadline"))
    with pytest.raises(TimeoutError):
        service.search(ObservationQuery(**params(county=["13081"])), checkpoint=checkpoint)
    repository.current_release.assert_not_called()
    producer.assert_not_called()


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
    expected_key = "tick" if measure == "scapularis_status" else "pathogen"
    assert item["source_id"] == item["evidence"]["source_id"] == expected_key
    assert item["lineage_source_id"] == "cdc_arbonet_tick_module"
    assert item["source_label"] is item["methodology_id"] is item["methodology"] is None
    assert item["source_vintage"] == "2025"
    assert item["denominator"] == "NONE"
    assert item["methodology_version"] and item["release_methodology_version"]
    assert item["limitations"] == [
        x["text"] for x in envelope(measure, county)["semantic"]["measure"]["limitations"]
    ]
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


@pytest.mark.parametrize("measure", list(IDENTITIES))
@pytest.mark.parametrize("county", ["01001", "06001", "16001", "13081", "12047"])
@pytest.mark.parametrize("year", [2023, 2025])
def test_annual_coercion_rejected_without_data_access(measure, county, year):
    query = params(measure, county)
    query.pop("start_date")
    query.pop("end_date")
    query["year"] = year
    repository = Mock()
    producer = Mock()
    api = create_app(settings=settings(), observation_repository=repository)
    api.state.observation_service = ObservationService(
        repository, cdc_adapter=CDCObservationAdapter(settings(), producer)
    )
    response = TestClient(api).get("/v1/observations", params=query)
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "UNSUPPORTED_FILTER"
    assert "start_date=end_date=2025-12-31" in response.json()["detail"]
    assert response.headers["x-request-id"] == response.json()["request_id"]
    repository.current_release.assert_not_called()
    producer.assert_not_called()


@pytest.mark.parametrize("measure", list(IDENTITIES))
def test_wrong_cumulative_period_is_client_error(measure):
    producer = Mock()
    response = client(producer).get(
        "/v1/observations", params={**params(measure), "start_date": "2025-12-30"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "UNSUPPORTED_FILTER"
    producer.assert_not_called()


def test_invalid_cdc_requests_still_consume_public_rate_budget():
    api = client(config=settings(public_rate_limit_per_minute=2))
    query = params()
    query.pop("start_date")
    query.pop("end_date")
    query["year"] = 2023
    for _ in range(2):
        assert api.get("/v1/observations", params=query).status_code == 400
    response = api.get("/v1/observations", params=query)
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "measure,county",
    [
        ("scapularis_status", "01001"),
        ("scapularis_status", "01003"),
        ("burgdorferi_status", "06001"),
        ("burgdorferi_status", "06013"),
    ],
)
def test_additional_producer_admitted_counties_succeed(measure, county):
    # Injected admitted envelopes prove routing, not live PROD admission.
    producer = Mock(side_effect=envelope)
    response = client(producer).get("/v1/observations", params=params(measure, county))
    assert response.status_code == 200
    assert response.json()["data"][0]["geography"]["geography_id"] == county
    producer.assert_called_once_with(measure, county)


def test_unknown_coverage_placeholder_fails_closed():
    result = envelope("scapularis_status", "02122")
    result["semantic"]["observation"].update(value="Unknown", value_state="UNKNOWN")
    result["surveillance_evidence"].update(value_state="UNKNOWN", normalized_state="unknown")
    response = client(lambda *_: result).get("/v1/observations", params=params(county="02122"))
    assert response.status_code == 503
    assert "surveillance_context" not in response.text


def test_published_record_without_exact_data_packet_binding_is_unavailable():
    from lyme_gap_atlas_data import cdc_consumer_runtime

    metadata = cdc_consumer_runtime._metadata(
        cdc_consumer_runtime._resources(), "scapularis_status", RELEASE
    )
    packet = {
        "release_id": RELEASE,
        "bundle_sha256": BUNDLE,
        "scope": cdc_consumer_runtime.SLICES["scapularis_status"],
        "metadata_revision_id": metadata["revision_id"],
        "reviewed_at": "2026-10-17",
        "decision_reference": "retained-steward-decision",
        "observation_bindings": {"different-observation": {"record_id": "13081"}},
    }
    facts = {
        "release_status": "PUBLISHED",
        "source_status": "APPROVED",
        "review_decisions": [
            {
                "decision": "APPROVED",
                "active_steward": True,
                "reviewer": "steward",
                "decision_id": "retained-decision",
                "rationale": "approved exact record bindings",
                "decided_at": metadata["steward_review"]["reviewed_at"]["value"],
                "conditions": [{"cdc_consumer_admission": packet}],
            }
        ],
    }

    def producer(*_):
        return cdc_consumer_runtime._packet(
            facts, metadata, {"release_id": RELEASE, "observation_id": "unadmitted"}
        )

    with pytest.raises(
        cdc_consumer_runtime.CDCConsumerUnavailable,
        match="exact retained steward/release decision required",
    ):
        producer()
    response = client(producer).get("/v1/observations", params=params(county="01001"))
    assert response.status_code == 503
    assert "retained-decision" not in response.text
    assert "surveillance_context" not in response.text


def test_page_bounds_and_cancellation_stop_producer_fanout():
    producer = Mock(side_effect=envelope)
    adapter = CDCObservationAdapter(settings(), producer)
    query = ObservationQuery(**params(county=["01001", "01003", "01005", "13081"], page_size=1))
    items = adapter.search(query, RELEASE, 1)
    assert len(items) == 2
    assert producer.call_args_list == [
        (("scapularis_status", "01003"),),
        (("scapularis_status", "01005"),),
    ]
    producer.reset_mock()
    checkpoint = Mock(side_effect=[None, TimeoutError("deadline")])
    with pytest.raises(TimeoutError):
        adapter.search(query, RELEASE, 0, checkpoint=checkpoint)
    producer.assert_called_once_with("scapularis_status", "01001")


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


def test_historical_release_is_never_an_automatic_fallback():
    producer = Mock(side_effect=envelope)
    adapter = CDCObservationAdapter(settings(), producer)
    with pytest.raises(AtlasDataUnavailableError):
        adapter.search(ObservationQuery(**params()), "governed-2026-10-06-nlcd-demo-cohort", 0)
    producer.assert_not_called()


def test_prior_metadata_revision_cannot_serve_current_release():
    result = envelope("scapularis_status", "01001")
    result["semantic"]["measure"]["metadata_revision"] = 2
    response = client(lambda *_: result).get("/v1/observations", params=params(county="01001"))
    assert response.status_code == 503


@pytest.mark.parametrize(
    "measure,county,key",
    [
        ("scapularis_status", "13081", "tick"),
        ("burgdorferi_status", "19135", "pathogen"),
        ("burgdorferi_status", "12047", "pathogen"),
    ],
)
def test_emitted_source_key_resolves_through_existing_catalog_endpoint(measure, county, key):
    from test_public_provenance import OTHER
    from test_public_provenance import FixtureRepository as SourceFixture

    class Catalog(SourceFixture):
        release = RELEASE

        def source(self, source_id, release):
            assert release == RELEASE
            if source_id != key:
                return None
            result = list(OTHER)
            result[1], result[2] = "Fixture CDC source", "through 2025-12-31"
            result[0], result[5], result[6], result[11] = (
                key,
                "cdc_arbonet_tick_module",
                IDENTITIES[measure][0],
                RELEASE,
            )
            return tuple(result)

    api = client(provenance_repository=Catalog())
    observation = api.get("/v1/observations", params=params(measure, county)).json()["data"][0]
    response = api.get(f"/v1/sources/{observation['source_id']}")
    assert response.status_code == 200
    source = response.json()["data"]
    assert source["source_id"] == key == observation["evidence"]["source_id"]
    assert source["release_version"] == observation["release_id"] == RELEASE
    assert source["lineage_source_id"] == observation["lineage_source_id"]
    assert source["dataset_id"] == observation["dataset_id"]
