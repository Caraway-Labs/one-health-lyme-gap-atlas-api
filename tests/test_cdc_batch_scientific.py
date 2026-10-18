"""Real Data producer parity using synthetic admission fixtures from reviewed Data SHA.

These fixtures establish engineering parity only, never live PROD admission.
"""

import copy
import json
from pathlib import Path

import pytest
from lyme_gap_atlas_data import cdc_consumer_batch as batch
from lyme_gap_atlas_data import cdc_consumer_runtime as singleton

from lyme_gap_atlas_api import cdc_surveillance as cdc
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_contract import ObservationQuery
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError

FIXTURE = Path(__file__).parent / "fixtures/cdc_batch/reviewed-data-cases.json"
IDENTITY = {
    "USER": "SYNTHETIC",
    "ROLE": "OH_LYME_PROD_READ",
    "DATABASE": "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
    "WAREHOUSE": "COMPUTE_WH",
}


@pytest.fixture(params=range(4))
def case(request):
    document = json.loads(FIXTURE.read_text())
    assert document["source_commit"] == "2476bfec3b89b513adf9ce2348fe94be44abf915"
    return document["cases"][request.param]


def envelope(rows):
    shared = {key: copy.deepcopy(rows[0]["cdc_runtime_authority"][key]) for key in batch._SHARED}
    records = copy.deepcopy(rows)
    for row in records:
        for key in batch._SHARED:
            row["cdc_runtime_authority"].pop(key)
    return {"shared_authority": shared, "shared_consistent": True, "records": records}


def callback(rows, payload=None):
    def query(sql, params):
        if sql == singleton.IDENTITY_SQL:
            return [copy.deepcopy(IDENTITY)]
        if sql == singleton.AUTHORITY_SQL:
            return [copy.deepcopy(next(row for row in rows if row["county_fips"] == params[2]))]
        assert sql == batch._authority_sql(len(rows))
        return [
            {"BATCH_AUTHORITY": copy.deepcopy(payload if payload is not None else envelope(rows))}
        ]

    return query


def settings():
    return ApiSettings(
        _env_file=None,
        snowflake_auth_method="key_pair",
        snowflake_user="OH_LYME_API_SVC",
        snowflake_role="OH_LYME_PROD_READ",
        snowflake_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        snowflake_warehouse="COMPUTE_WH",
    )


def test_real_batch_singleton_and_public_dto_exact_parity(case):
    measure, rows = case["measure"], case["rows"]
    counties = tuple(row["county_fips"] for row in reversed(rows))
    query = callback(rows)
    result = batch.produce_cdc_batch_v1(
        query, measure, counties, environment="prod", release_id=cdc.RELEASE
    )
    expected = tuple(
        singleton._project(
            query, singleton._resources(), measure, county, "prod", release_id=cdc.RELEASE
        )
        for county in counties
    )
    assert result.observations == expected
    assert result.county_fips == counties
    adapter = cdc.CDCObservationAdapter(settings())
    for county, actual, prior in zip(counties, result.observations, expected, strict=True):
        dto = adapter._map_observation(measure, county, actual)
        assert (
            dto.model_dump_json()
            == adapter._map_observation(measure, county, prior).model_dump_json()
        )
        assert dto.value == case["value"]
        assert dto.metadata_revision_id == cdc.IDENTITIES[measure][2]
        assert dto.release_id == cdc.RELEASE
        assert dto.limitations and dto.evidence and dto.surveillance_context
        expected_state = {
            "Established": "established",
            "Reported": "detected",
            "Present": "detected",
            "No records": "unknown",
        }[case["value"]]
        assert actual["surveillance_evidence"]["normalized_state"] == expected_state
    safe = json.dumps(result.observations)
    for private in (
        "observation_bindings",
        "review_decisions",
        "attestations",
        "donor_observation_id",
        "conformed_record_id",
    ):
        assert private not in safe


@pytest.mark.parametrize(
    "tamper", ["missing", "duplicate", "unauthorized", "unknown_coverage", "metadata", "release"]
)
def test_real_producer_failure_has_no_partial_response_or_singleton_fallback(case, tamper):
    rows = case["rows"]
    payload = envelope(rows)
    if tamper == "missing":
        payload["records"].pop()
    elif tamper == "duplicate":
        payload["records"][1] = copy.deepcopy(payload["records"][0])
    elif tamper == "release":
        payload["records"][1]["release_id"] = "wrong"
    elif tamper == "unknown_coverage":
        payload["records"][1]["cdc_runtime_authority"]["coverage_state"] = "UNKNOWN_SOURCE_COVERAGE"
    else:
        review = payload["shared_authority"]["review_decisions"][0]
        if tamper == "unauthorized":
            review["active_steward"] = False
        else:
            review["conditions"][0]["cdc_consumer_admission"]["metadata_revision_id"] = "wrong"
    attempted = []

    def produce(measure, counties):
        attempted.append(counties)
        return batch.produce_cdc_batch_v1(
            callback(rows, payload), measure, counties, environment="prod", release_id=cdc.RELEASE
        )

    def forbidden_singleton(*args):
        pytest.fail("failed batch must not retry singleton admission")

    adapter = cdc.CDCObservationAdapter(settings(), forbidden_singleton, batch_producer=produce)
    query = ObservationQuery(
        measure_id=case["measure"],
        geography_type="county",
        geography_id=[row["county_fips"] for row in rows],
        start_date="2025-12-31",
        end_date="2025-12-31",
        page_size=2,
    )
    with pytest.raises(AtlasDataUnavailableError, match="Governed CDC surveillance is unavailable"):
        adapter.search(query, cdc.RELEASE, 0)
    assert len(attempted) == 1
