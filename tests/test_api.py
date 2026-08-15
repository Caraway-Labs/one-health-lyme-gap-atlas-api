from datetime import UTC, datetime

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.models import AtlasMetadata, CountyRecord, SourceMetadata
from lyme_gap_atlas_api.repository import Snapshot


class FakeRepository:
    def ready(self) -> bool:
        return True

    def load_snapshot(self) -> Snapshot:
        metadata = AtlasMetadata(
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
            states=[{"code": "CO", "name": "Colorado"}],
        )
        county = CountyRecord(
            release_id=metadata.release_id,
            fips="08001",
            county="Adams",
            state="CO",
            state_name="Colorado",
            population=500_000,
            in_contiguous_tick_scope=True,
            human_status="no_county_linked_record",
            case_count_floor_2023=None,
            incidence_floor_2023=None,
            state_unallocated_records_2023=1,
            tick_status="Established",
            scapularis_status="Established",
            pacificus_status="No records",
            burgdorferi_status="Present",
            svi_percentile=0.5,
            uninsured_percentile=0.5,
            uninsured_percent=8.0,
            rucc_2023=2,
            evidence_completeness=6,
            geometry={"type": "Polygon", "coordinates": []},
        )
        return Snapshot(metadata=metadata, counties=[county])


def client() -> TestClient:
    settings = ApiSettings(
        snowflake_account="test",
        snowflake_user="test",
        snowflake_role="test",
        snowflake_pat="test",
        cors_origins=["https://carawaylabs.com"],
        rate_limit_per_minute=100,
    )
    return TestClient(create_app(FakeRepository(), settings))


def test_health_and_contract() -> None:
    api = client()
    assert api.get("/health/live").json() == {"status": "ok"}
    assert api.get("/health/ready").status_code == 200
    assert api.get("/openapi.json").status_code == 200


def test_scores_geometry_detail_and_csv() -> None:
    api = client()
    score = api.get("/v1/atlas/scores").json()["counties"][0]
    assert score["fips"] == "08001"
    assert score["score"]["human_weakness"] == 75
    assert api.get("/v1/atlas/geometry").headers["cache-control"].endswith("immutable")
    assert api.get("/v1/counties/08001").json()["release"]["sources"][0]["key"] == "human"
    assert "Adams" in api.get("/v1/atlas/ranking.csv?state=CO").text


def test_validation_and_unknown_release() -> None:
    api = client()
    invalid = api.get("/v1/atlas/scores?ecological_share=63")
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
    assert api.get("/v1/atlas/metadata?dataset_version=missing").status_code == 404
