"""Fixture contract proof, independent of live source/publication acceptance."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.environmental_context import (
    FIELDS,
    NUMERIC_FIELDS,
    EnvironmentalRepository,
    decode_numerics,
    environmental_observation,
)
from lyme_gap_atlas_api.public_metadata import MetadataRows
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError


def fixture_row(fips="01001", value=0.0, state="ZERO", coverage="COMPLETE"):
    row = dict.fromkeys(FIELDS)
    row.update(
        release_id="fixture-release",
        measure_id="nclimgrid_prcp_county_day",
        semantic_version="2.0.0",
        metadata_revision_id="fixture-metadata",
        county_fips=fips,
        period_start=date(2025, 1, 1),
        period_end=date(2025, 1, 1),
        temporal_resolution="DAY",
        day_convention="Labeled 24-hour period ending in the early morning; not a midnight "
        "calendar day",
        value=value,
        value_state=state,
        unit="mm",
        coverage_status=coverage,
        source_time_present=True,
        atlas_acquired_at=datetime(2026, 9, 28, tzinfo=UTC),
        publisher="NOAA NCEI",
        dataset_id="nclimgrid-daily-v1.0.0-scaled",
        source_vintage="v1.0.0-scaled-202501",
        methodology_version="atlas-nclimgrid-county-day/2",
        weight_version="fixture-weight",
        geometry_version="fixture-tiger-2025",
        limitations=json.dumps(
            [
                {
                    "text": "January 2025 descriptive weather only; no Lyme causal, risk or ML "
                    "claim."
                },
                {
                    "text": "Historical first availability unknown; labeled day differs from "
                    "midnight calendar day."
                },
            ]
        ),
    )
    for field in NUMERIC_FIELDS:
        if field != "value":
            row[field] = 1.0
        row[field + "_stored_type"] = "DOUBLE" if row[field] is not None else "NULL_VALUE"
        row[field + "_native_double"] = row[field]
        row[field] = json.dumps(row[field]) if row[field] is not None else None
    return tuple(row[k] for k in FIELDS)


class FixtureRepository:
    release = "fixture-release"

    def current_release(self):
        return self.release

    def measure_exists(self, measure, release):
        return measure == "nclimgrid_prcp_county_day"

    def query(self, query, release, offset):
        rows = [
            fixture_row(),
            fixture_row("01003", None, "MISSING", "PARTIAL_COVERAGE"),
            fixture_row("02001", None, "UNAVAILABLE", "OUT_OF_SOURCE_COVERAGE"),
        ]
        rows = [r for r in rows if r[FIELDS.index("county_fips")] in query.geography_id]
        return rows[offset : offset + query.page_size + 1]


def params(**extra):
    return {
        "measure_id": "nclimgrid_prcp_county_day",
        "geography_type": "county",
        "geography_id": ["01001", "01003", "02001"],
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        **extra,
    }


def test_daily_states_coverage_provenance_and_query_bound_token():
    repo = FixtureRepository()
    api = TestClient(create_app(settings=ApiSettings(), observation_repository=repo))
    response = api.get("/v1/observations", params=params(page_size=2))
    assert response.status_code == 200
    first, partial = response.json()["data"]
    assert (first["value"], first["value_state"]) == (0, "ZERO")
    assert first["temporal_grain"] == "DAY"
    assert first["source_published_at"] is None
    assert first["atlas_acquired_at"].startswith("2026-09-28")
    assert partial["value"] is None and partial["value_state"] == "MISSING"
    assert partial["environmental_context"]["coverage_status"] == "PARTIAL_COVERAGE"
    assert set(first).isdisjoint(
        {"payload", "artifact_id", "capture_record_id", "ingestion_run_id"}
    )
    assert all(
        not k.endswith(("stored_type", "native_double")) for k in first["environmental_context"]
    )
    token = response.json()["meta"]["next_page_token"]
    second = api.get("/v1/observations", params=params(page_size=2, page_token=token))
    assert second.json()["data"][0]["value_state"] == "UNAVAILABLE"
    assert second.json()["meta"]["next_page_token"] is None
    repo.release = "changed-release"
    assert api.get("/v1/observations", params=params(page_token=token)).status_code == 400
    assert api.get("/v1/observations", params=params(stratification="age")).status_code == 400
    assert api.get("/v1/observations", params=params(sort="value")).status_code == 400
    assert (
        api.get("/v1/observations", params=params(end_date="2025-02-01"), headers={}).status_code
        == 200
    )


def test_daily_year_selection_counts_days_against_ceiling():
    api = TestClient(
        create_app(
            settings=ApiSettings(public_query_result_ceiling=10),
            observation_repository=FixtureRepository(),
        )
    )
    assert (
        api.get(
            "/v1/observations",
            params={
                "measure_id": "nclimgrid_prcp_county_day",
                "geography_type": "county",
                "geography_id": "01001",
                "year": 2025,
            },
        ).status_code
        == 400
    )


def test_disabled_environmental_reads_never_open_connection(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled adapter opened a connection")

    monkeypatch.setattr("lyme_gap_atlas_api.environmental_context.connect", forbidden)
    with pytest.raises(AtlasDataUnavailableError):
        EnvironmentalRepository(ApiSettings()).metadata()


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_every_numeric_field_preserves_native_double_exact_decimal_and_null(field):
    row = dict(zip(FIELDS, fixture_row(), strict=True))
    row[field] = "0.123456789012345678901234567890"
    row[field + "_stored_type"] = "DECIMAL"
    row[field + "_native_double"] = None
    assert decode_numerics(row)[field] == "0.123456789012345678901234567890"
    row[field] = "0.12345678901234568"
    row[field + "_stored_type"] = "DOUBLE"
    row[field + "_native_double"] = 0.12345678901234567
    assert decode_numerics(row)[field] == 0.12345678901234567
    row[field] = None
    row[field + "_stored_type"] = "NULL_VALUE"
    row[field + "_native_double"] = None
    assert decode_numerics(row)[field] is None
    row[field] = "null"
    with pytest.raises(ValueError):
        decode_numerics(row)


def test_negative_temperature_and_invalid_state_fail_closed():
    row = dict(zip(FIELDS, fixture_row(value=-2.5, state="OBSERVED"), strict=True))
    row["measure_id"] = "nclimgrid_tmin_county_day"
    row["unit"] = "degree_Celsius"
    assert environmental_observation(tuple(row[k] for k in FIELDS)).value == -2.5
    row["value_state"] = "ZERO"
    with pytest.raises(AtlasDataUnavailableError):
        environmental_observation(tuple(row[k] for k in FIELDS))
    row["value_state"] = "OBSERVED"
    row["value_native_double"] = Decimal("-2.5")
    with pytest.raises(AtlasDataUnavailableError):
        environmental_observation(tuple(row[k] for k in FIELDS))


class MetadataFixture:
    def __init__(self, broken=None):
        self.broken = broken

    def load_metadata(self):
        climate = []
        for m in ("prcp", "tmin", "tmax", "tavg"):
            climate.append(
                (
                    "fixture-release",
                    f"nclimgrid_{m}_county_day",
                    "climate_precipitation" if m == "prcp" else "climate_temperature",
                    "2.0.0",
                    "fixture-revision",
                    f"Fixture {m}",
                    "Fixture weather definition",
                    "mm" if m == "prcp" else "degree_Celsius",
                    None,
                    "COUNTY",
                    "DAY",
                    '["OBSERVED","ZERO","MISSING","UNAVAILABLE"]',
                    "atlas-nclimgrid-county-day/2",
                    '[{"text":"Fixture descriptive limitation"}]',
                )
            )
        if self.broken == "partial":
            climate.pop()
        if self.broken == "empty":
            climate = []
        if self.broken == "disabled":
            climate = None
        if self.broken == "release":
            climate[0] = ("other-release", *climate[0][1:])
        if self.broken == "states":
            climate[0] = (*climate[0][:11], "malformed", *climate[0][12:])
        return MetadataRows(
            [("annual", "Annual", None, None, None, None, "1.0.0", "fixture-release")],
            [
                (
                    "annual_measure",
                    "annual",
                    "Annual",
                    None,
                    "NUMBER",
                    "cases",
                    None,
                    "COUNTY",
                    "YEAR",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "1.0.0",
                    "fixture-release",
                )
            ],
            climate,
        )


def test_metadata_discovery_uses_canonical_ids_and_preserves_annual_meaning():
    api = TestClient(create_app(settings=ApiSettings(), metadata_repository=MetadataFixture()))
    response = api.get("/v1/measures")
    assert response.status_code == 200
    measures = {m["measure_id"]: m for m in response.json()["data"]}
    assert len(measures) == 5
    assert measures["annual_measure"]["semantic_version"] == "1.0.0"
    daily = measures["nclimgrid_tavg_county_day"]
    assert daily["semantic_version"] == "2.0.0" and daily["temporal_grains"] == ["DAY"]
    assert daily["source_ids"] == ["noaa_nclimgrid_daily_202501"]
    assert api.get("/v1/measures/nclimgrid_tavg_county_day").status_code == 200
    assert api.get("/v1/indicators/climate_temperature").json()["data"]["measure_ids"] == [
        "nclimgrid_tavg_county_day",
        "nclimgrid_tmax_county_day",
        "nclimgrid_tmin_county_day",
    ]


@pytest.mark.parametrize("broken", ["partial", "release", "states", "empty"])
def test_incomplete_or_mixed_release_metadata_returns_sanitized_503(broken):
    api = TestClient(
        create_app(settings=ApiSettings(), metadata_repository=MetadataFixture(broken))
    )
    response = api.get("/v1/measures")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert "malformed" not in response.text and "other-release" not in response.text


def test_disabled_climate_metadata_preserves_annual_only_discovery():
    api = TestClient(
        create_app(settings=ApiSettings(), metadata_repository=MetadataFixture("disabled"))
    )
    response = api.get("/v1/measures")
    assert response.status_code == 200
    assert [m["measure_id"] for m in response.json()["data"]] == ["annual_measure"]


def test_environmental_query_binds_all_inputs_and_has_view_only_boundary(monkeypatch):
    from lyme_gap_atlas_api.public_contract import GeographyType, ObservationQuery

    repository = EnvironmentalRepository(
        ApiSettings(
            environmental_context_enabled=True,
            snowflake_presentation_database="ONE_HEALTH_LYME_GAP_ATLAS_DEV",
        )
    )
    calls = []
    monkeypatch.setattr(repository, "_read", lambda sql, params: calls.append((sql, params)) or [])
    query = ObservationQuery(
        measure_id="nclimgrid_prcp_county_day",
        geography_type=GeographyType.county,
        geography_id=["01003", "01001"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        page_size=20,
    )
    repository.query(query, "fixture-release", 0)
    sql, binds = calls[0]
    assert "CURRENT_CLIMATE_COUNTY_DAY_OBSERVATIONS_V" in sql
    assert "GOVERNANCE" not in sql and "RAW" not in sql
    assert binds == (
        "nclimgrid_prcp_county_day",
        "01001",
        "01003",
        date(2025, 1, 1),
        date(2025, 1, 31),
        "fixture-release",
        21,
        0,
    )
    assert "01001" not in sql and "fixture-release" not in sql
