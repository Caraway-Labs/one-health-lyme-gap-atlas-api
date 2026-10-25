"""GET /v1/atlas/map-values serves cached snapshot map layers without observations."""

from datetime import UTC, datetime
from unittest.mock import Mock

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.models import AtlasMetadata, CountyRecord, SourceMetadata
from lyme_gap_atlas_api.repository import Snapshot


def _metadata() -> AtlasMetadata:
    return AtlasMetadata(
        release_id="alpha-2026-08-06",
        schema_version="0.2.0",
        generated_at=datetime(2026, 8, 6, tzinfo=UTC),
        loaded_at=datetime.now(UTC),
        scope="United States counties",
        bundle_sha256="a" * 64,
        score_defaults={"ecological_share": 0.65},
        methodology_version="alpha-0.2.0",
        limitations="Not individual risk.",
        sources=[
            SourceMetadata(
                key="human",
                label="CDC",
                vintage="2023",
                url="https://cdc.gov",
                note="Published floor",
            )
        ],
        states=[{"code": "CO", "name": "Colorado"}, {"code": "AK", "name": "Alaska"}],
    )


def _county(
    *,
    fips: str,
    incidence_floor_2023: float | None,
    svi_percentile: float | None,
    uninsured_percent: float | None,
    rucc_2023: int | None,
    state: str = "CO",
) -> CountyRecord:
    metadata = _metadata()
    return CountyRecord(
        release_id=metadata.release_id,
        fips=fips,
        county="Fixture",
        state=state,
        state_name="Colorado" if state == "CO" else "Alaska",
        population=10_000,
        in_contiguous_tick_scope=state == "CO",
        human_status="no_county_linked_record",
        case_count_floor_2023=None,
        incidence_floor_2023=incidence_floor_2023,
        state_unallocated_records_2023=None,
        tick_status="Established",
        scapularis_status="Established",
        pacificus_status="No records",
        burgdorferi_status="Present",
        svi_percentile=svi_percentile,
        uninsured_percentile=0.5,
        uninsured_percent=uninsured_percent,
        rucc_2023=rucc_2023,
        evidence_completeness=6,
        geometry={"type": "Polygon", "coordinates": []},
    )


class MapValuesRepository:
    def ready(self) -> bool:
        return True

    def load_snapshot(self) -> Snapshot:
        metadata = _metadata()
        counties = [
            _county(
                fips="08001",
                incidence_floor_2023=None,
                svi_percentile=0.0,
                uninsured_percent=0.0,
                rucc_2023=3,
            ),
            _county(
                fips="02013",
                incidence_floor_2023=12.5,
                svi_percentile=0.2663,
                uninsured_percent=7.4,
                rucc_2023=None,
                state="AK",
            ),
            _county(
                fips="01001",
                incidence_floor_2023=4.2,
                svi_percentile=0.5,
                uninsured_percent=8.0,
                rucc_2023=2,
                state="AL",
            ),
        ]
        return Snapshot(metadata=metadata, counties=counties)


def _client(repository: MapValuesRepository | None = None) -> TestClient:
    settings = ApiSettings(
        snowflake_account="test",
        snowflake_user="test",
        snowflake_role="test",
        snowflake_pat="test",
    )
    return TestClient(create_app(repository or MapValuesRepository(), settings))


def test_map_values_returns_sorted_snapshot_fields_without_geometry() -> None:
    api = _client()
    response = api.get("/v1/atlas/map-values?dataset_version=alpha-2026-08-06")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["etag"].startswith('"')

    body = response.json()
    assert body["release_id"] == "alpha-2026-08-06"
    assert [county["fips"] for county in body["counties"]] == ["01001", "02013", "08001"]
    assert set(body["counties"][0]) == {
        "fips",
        "incidence_floor_2023",
        "svi_percentile",
        "uninsured_percent",
        "rucc_2023",
    }

    by_fips = {county["fips"]: county for county in body["counties"]}
    assert by_fips["08001"]["incidence_floor_2023"] is None
    assert by_fips["08001"]["svi_percentile"] == 0.0
    assert by_fips["08001"]["uninsured_percent"] == 0.0
    assert by_fips["08001"]["rucc_2023"] == 3
    assert by_fips["02013"]["rucc_2023"] is None
    assert "geometry" not in body
    assert "uninsured_percentile" not in str(body)


def test_map_values_unknown_dataset_version_returns_404() -> None:
    api = _client()
    response = api.get("/v1/atlas/map-values?dataset_version=missing")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_map_values_reuses_cached_snapshot_without_reload() -> None:
    repository = MapValuesRepository()
    repository.load_snapshot = Mock(side_effect=repository.load_snapshot)
    api = _client(repository)

    first = api.get("/v1/atlas/map-values")
    second = api.get("/v1/atlas/map-values")

    assert first.status_code == second.status_code == 200
    assert repository.load_snapshot.call_count == 1
