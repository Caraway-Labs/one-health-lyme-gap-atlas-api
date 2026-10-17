"""Synthetic serving fixtures based on reviewed V155 fields; not runtime proof."""

import copy
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.county_context import FIELDS, context_observation
from lyme_gap_atlas_api.county_context_ids import CONTEXT_CONTRACT, CONTEXT_FIELDS
from lyme_gap_atlas_api.public_contract import ObservationQuery
from lyme_gap_atlas_api.public_observations import (
    ObservationService,
    SnowflakeObservationRepository,
)
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError

FIXTURE = json.loads((Path(__file__).parent / "fixtures/county-context-v1.json").read_text())
RELEASE = FIXTURE[0]["release_version"]


def row(mid="rucc_2023", fips="01001", **changes):
    record = copy.deepcopy(
        next(r for r in FIXTURE if r["measure_id"] == mid and r["county_fips"] == fips)
    )
    record.update(changes)
    for key in ("period_start", "period_end"):
        if isinstance(record[key], str):
            record[key] = date.fromisoformat(record[key])
    record["retrieved_at"] = datetime.fromisoformat(record["retrieved_at"])
    return tuple(record[k] for k in FIELDS)


class Repository:
    def current_release(self):
        return RELEASE

    def measure_exists(self, mid, release):
        return mid in CONTEXT_FIELDS

    def query(self, query, release, offset):
        rows = [
            row(query.measure_id, fips)
            for fips in sorted(query.geography_id)
            if any(
                r["measure_id"] == query.measure_id and r["county_fips"] == fips for r in FIXTURE
            )
        ]
        return rows[offset : offset + query.page_size + 1]


def params(mid="rucc_2023", **changes):
    return {
        "measure_id": mid,
        "geography_type": "county",
        "geography_id": ["01001", "08001"],
        "vintage_year": 2023 if mid == "rucc_2023" else 2022,
        **changes,
    }


def client(repository=None):
    return TestClient(
        create_app(settings=ApiSettings(), observation_repository=repository or Repository())
    )


@pytest.mark.parametrize("mid", list(CONTEXT_FIELDS))
def test_five_measures_two_counties_and_provenance(mid):
    response = client().get("/v1/observations", params=params(mid))
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert len(rows) == 2
    for result, fips in zip(rows, ("01001", "08001"), strict=True):
        fixture = next(r for r in FIXTURE if r["measure_id"] == mid and r["county_fips"] == fips)
        assert str(result["value"]) == fixture["value_json"]
        assert result["county_context"]["source_field"] == CONTEXT_FIELDS[mid]
        assert result["county_context"]["vintage_year"] == params(mid)["vintage_year"]
        assert result["metadata_revision_id"] == fixture["metadata_revision_id"]
        assert result["release_id"] == RELEASE
        assert result["geography"]["geography_id"] == fips
        assert result["source_published_at"] is None
        assert result["unit"] == fixture["unit"] and result["limitations"]
        if mid == "rucc_2023":
            assert type(result["value"]) is int
            assert result["period_start"] is None and result["period_end"] is None
            assert result["county_context"]["category_label"] == fixture["category_label"]
        else:
            assert (result["period_start"], result["period_end"]) == ("2018-01-01", "2022-12-31")
    for forbidden in (
        "ingestion_run_id",
        "artifact_id",
        "source_record_id",
        "source_row_hash",
        "geometry_json",
    ):
        assert forbidden not in response.text


@pytest.mark.parametrize("code", range(1, 10))
def test_rucc_preserves_all_category_codes_and_source_labels(code):
    # Frozen DATA200 codebook fixtures; the API transports, never classifies.
    labels = (
        "Metro: area population at least 1 million",
        "Metro: area population 250,000 to 1 million",
        "Metro: area population below 250,000",
        "Nonmetro: urban population at least 20,000; adjacent",
        "Nonmetro: urban population at least 20,000; nonadjacent",
        "Nonmetro: urban population 5,000 to 20,000; adjacent",
        "Nonmetro: urban population 5,000 to 20,000; nonadjacent",
        "Nonmetro: urban population below 5,000; adjacent",
        "Nonmetro: urban population below 5,000; nonadjacent",
    )
    observation = context_observation(row(value_json=str(code), category_label=labels[code - 1]))
    assert observation.value == code
    assert observation.county_context.category_label == labels[code - 1]


@pytest.mark.parametrize("mid", [m for m in CONTEXT_FIELDS if m != "rucc_2023"])
@pytest.mark.parametrize("state,value", [("ZERO", "0"), ("MISSING", None)])
def test_genuine_zero_and_missing_remain_distinct(mid, state, value):
    observation = context_observation(row(mid, value_json=value, value_state=state))
    assert observation.value == (0 if state == "ZERO" else None)
    assert observation.value_state.value == state


@pytest.mark.parametrize("state,value", [("ZERO", "0"), ("MISSING", None)])
def test_http_union_preserves_zero_and_missing_context(state, value):
    class StateRepository(Repository):
        def query(self, query, release, offset):
            return [row(query.measure_id, value_json=value, value_state=state)]

    response = client(StateRepository()).get(
        "/v1/observations", params=params("svi_percentile_2022", geography_id="01001")
    )
    assert response.status_code == 200
    result = response.json()["data"][0]
    assert result["value"] == (0 if state == "ZERO" else None)
    assert result["value_state"] == state and result["county_context"]


@pytest.mark.parametrize(
    "changes",
    [
        {"value_json": "0"},
        {"value_json": "10"},
        {"value_json": "1.5"},
        {"value_json": "true"},
        {"value_json": "null"},
        {"value_state": "UNAVAILABLE"},
        {"period_start": date(2023, 1, 1)},
        {"vintage_year": 2022},
        {"source_field": "EP_UNINSUR"},
        {"source_id": "private_source"},
        {"semantic_version": "1.0.0"},
        {"context_contract_version": "unreviewed"},
        {"context_limitations": "[]"},
        {"context_limitations": '[{"text":""}]'},
        {"allowed_value_states": '["OBSERVED","ZERO"]'},
        {"unit": "percentile"},
    ],
)
def test_malformed_context_fails_closed(changes):
    with pytest.raises(AtlasDataUnavailableError):
        context_observation(row(**changes))


@pytest.mark.parametrize(
    "mid,changes",
    [
        ("svi_percentile_2022", {"value_json": "1.1"}),
        ("uninsured_percent_2022", {"value_json": "101"}),
        ("population_2022", {"value_json": "3.5"}),
        ("population_2022", {"value_json": "-1"}),
        ("svi_percentile_2022", {"period_start": date(2022, 1, 1)}),
    ],
)
def test_quantities_are_not_percentiles_or_categories(mid, changes):
    with pytest.raises(AtlasDataUnavailableError):
        context_observation(row(mid, **changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"vintage_year": 2022},
        {"year": 2023},
        {"start_date": "2023-01-01", "end_date": "2023-12-31"},
        {"geography_id": "1001"},
        {"geography_id": ["01001", "01001"]},
        {"geography_type": "state"},
        {"stratification": "age"},
    ],
)
def test_unsupported_filters_fail_before_data_access(changes):
    repository = MagicMock()
    response = client(repository).get("/v1/observations", params=params(**changes))
    assert response.status_code in (400, 422)
    repository.current_release.assert_not_called()


def test_missing_or_unknown_context_is_explicit_not_zero():
    api = client()
    assert api.get("/v1/observations", params=params(geography_id="99999")).status_code == 404
    assert api.get("/v1/observations", params=params("future_measure")).status_code == 400
    missing = params()
    missing.pop("vintage_year")
    assert api.get("/v1/observations", params=missing).status_code == 400


def test_context_pagination_is_query_and_release_bound():
    api = client()
    first = api.get("/v1/observations", params=params(page_size=1)).json()
    token = first["meta"]["next_page_token"]
    second = api.get("/v1/observations", params=params(page_size=1, page_token=token))
    assert second.status_code == 200
    assert second.json()["data"][0]["geography"]["geography_id"] == "08001"
    assert (
        api.get("/v1/observations", params=params("population_2022", page_token=token)).status_code
        == 400
    )


@pytest.mark.parametrize("page_size", [1, 2])
def test_duplicate_context_including_lookahead_fails_closed(page_size):
    class DuplicateRepository(Repository):
        def query(self, query, release, offset):
            return [row(), row()]

    response = client(DuplicateRepository()).get(
        "/v1/observations", params=params(page_size=page_size)
    )
    assert response.status_code == 503
    assert "fixture-rucc" not in response.text


def test_context_query_is_bounded_parameterized_and_marker_filtered():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.cursor.return_value = cursor
    repository = SnowflakeObservationRepository(ApiSettings(), _connection=connection)
    query = ObservationQuery(**params(page_size=2))
    repository.query(query, RELEASE, 0)
    sql, parameters = cursor.execute.call_args.args
    assert "CURRENT_COUNTY_OBSERVATIONS_V" in sql and "context_contract_version=%s" in sql
    assert "LIMIT %s OFFSET %s" in sql and "01001" not in sql
    assert tuple(CONTEXT_FIELDS) == parameters[:5]
    assert CONTEXT_CONTRACT in parameters and RELEASE in parameters
    assert parameters[-2:] == (3, 0)
    assert "CONFORMED" not in sql and "SEMANTIC_OBSERVATIONS" not in sql


def test_context_does_not_invoke_scientific_surveillance_resolver():
    resolver = MagicMock()
    query = ObservationQuery(**params())
    ObservationService(Repository(), resolver).search(query)
    resolver.resolve.assert_not_called()


def test_existing_dated_page_tokens_keep_their_query_fingerprint():
    from test_public_observations import FixtureRepository
    from test_public_observations import client as dated_client
    from test_public_observations import params as dated_params

    query = ObservationQuery(**dated_params(page_size=1))
    old_shape = query.model_dump(mode="json", exclude={"page_token", "page_size", "vintage_year"})
    fingerprint = hashlib.sha256(json.dumps(old_shape, sort_keys=True).encode()).hexdigest()
    token = ObservationService._token(fingerprint, FixtureRepository.release, 1)
    response = dated_client().get(
        "/v1/observations", params=dated_params(page_size=1, page_token=token)
    )
    assert response.status_code == 200
    assert response.json()["data"][0]["geography"]["geography_id"] == "01003"
