from datetime import UTC, datetime

import pytest
from lyme_gap_atlas_shared import ScoreSettings

from lyme_gap_atlas_api.models import AtlasMetadata, CountyRecord, SourceMetadata
from lyme_gap_atlas_api.reports.models import ReportMetric
from lyme_gap_atlas_api.reports.service import ReportService
from lyme_gap_atlas_api.repository import Snapshot
from lyme_gap_atlas_api.service import AtlasService


class FakeRepository:
    def ready(self) -> bool:
        return True

    def load_snapshot(self) -> Snapshot:
        metadata = AtlasMetadata(
            release_id="alpha-2026-08-06",
            schema_version="0.2.0",
            generated_at=datetime(2026, 8, 6, tzinfo=UTC),
            loaded_at=datetime(2026, 8, 7, tzinfo=UTC),
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
        return Snapshot(
            metadata=metadata,
            counties=[
                CountyRecord(
                    release_id=metadata.release_id,
                    fips="08001",
                    county="Adams",
                    state="CO",
                    state_name="Colorado",
                    population=0,
                    in_contiguous_tick_scope=True,
                    human_status="no_county_linked_record",
                    case_count_floor_2023=None,
                    incidence_floor_2023=None,
                    state_unallocated_records_2023=0,
                    tick_status="Established",
                    scapularis_status="Established",
                    pacificus_status="No records",
                    burgdorferi_status="Present",
                    svi_percentile=None,
                    uninsured_percentile=0.0,
                    uninsured_percent=0.0,
                    rucc_2023=2,
                    evidence_completeness=6,
                    geometry={"type": "Polygon", "coordinates": []},
                )
            ],
        )


@pytest.fixture
def reports() -> ReportService:
    return ReportService(
        AtlasService(FakeRepository()),
        clock=lambda: datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
    )


def test_county_report_preserves_zero_missing_and_provenance(reports: ReportService) -> None:
    report = reports.county_report(
        "08001",
        ScoreSettings(),
        dataset_version="alpha-2026-08-06",
        template_version="county-v1",
    )

    assert report.population.value == 0
    assert report.population.availability == "available"
    case_count = next(item for item in report.human_health if item.key == "case_count_floor_2023")
    assert case_count.value is None
    assert case_count.availability == "unavailable"
    assert report.provenance.dataset_version == "alpha-2026-08-06"
    assert report.provenance.scoring_settings == ScoreSettings().model_dump()
    assert report.identity.template_version == "county-v1"
    assert report.identity.generated_at == datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def test_state_report_is_typed_and_uses_same_versioned_atlas_data(reports: ReportService) -> None:
    report = reports.state_report("CO", ScoreSettings(), dataset_version="alpha-2026-08-06")

    assert report.geography.identifier == "CO"
    assert report.geography.name == "Colorado"
    assert report.provenance.dataset_version == "alpha-2026-08-06"
    assert report.identity.template_version == "state-v1"
    assert next(item for item in report.summary if item.key == "county_count").value == 1


def test_report_metric_rejects_missing_values_marked_available() -> None:
    with pytest.raises(ValueError, match="available metrics require a value"):
        ReportMetric(key="x", label="X", value=None, availability="available")


def test_report_service_rejects_unknown_dataset_or_state(reports: ReportService) -> None:
    with pytest.raises(LookupError):
        reports.county_report("08001", ScoreSettings(), dataset_version="missing")
    with pytest.raises(KeyError):
        reports.state_report("ZZ", ScoreSettings())
