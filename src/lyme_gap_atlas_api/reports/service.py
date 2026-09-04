"""Build report contracts from the server-authoritative Atlas service."""

from collections.abc import Callable
from datetime import UTC, datetime
from statistics import fmean

from lyme_gap_atlas_shared import ScoreSettings

from ..models import AtlasMetadata, CountyDetail, CountyScoreSummary
from ..service import AtlasService
from .models import (
    CountyReport,
    ReportGeography,
    ReportIdentity,
    ReportMetric,
    ReportProvenance,
    ReportScore,
    ReportSource,
    StateReport,
)

_REPORT_VERSION = "v1"


class ReportService:
    """Creates renderer-independent reports without a second data-access path."""

    def __init__(
        self,
        atlas_service: AtlasService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._atlas_service = atlas_service
        self._clock = clock or (lambda: datetime.now(UTC))

    def county_report(
        self,
        fips: str,
        settings: ScoreSettings,
        dataset_version: str | None = None,
        template_version: str = "county-v1",
    ) -> CountyReport:
        detail = self._atlas_service.county(fips, settings, dataset_version)
        return CountyReport(
            identity=self._identity(template_version),
            geography=ReportGeography(
                level="county",
                identifier=detail.fips,
                name=detail.county,
                state_code=detail.state,
                state_name=detail.state_name,
            ),
            provenance=self._provenance(detail.release, settings),
            score=self._score(detail),
            priority=detail.priority,
            population=self._metric("population", "Population", detail.population),
            human_health=[
                self._metric("human_status", "Human surveillance status", detail.human_status),
                self._metric(
                    "case_count_floor_2023", "Case count floor (2023)", detail.case_count_floor_2023
                ),
                self._metric(
                    "incidence_floor_2023", "Incidence floor (2023)", detail.incidence_floor_2023
                ),
                self._metric(
                    "state_unallocated_records_2023",
                    "State-unallocated records (2023)",
                    detail.state_unallocated_records_2023,
                ),
            ],
            ecological=[
                self._metric("tick_status", "Tick status", detail.tick_status),
                self._metric("scapularis_status", "I. scapularis status", detail.scapularis_status),
                self._metric("pacificus_status", "I. pacificus status", detail.pacificus_status),
                self._metric(
                    "burgdorferi_status", "B. burgdorferi status", detail.burgdorferi_status
                ),
                self._metric("svi_percentile", "SVI percentile", detail.svi_percentile),
                self._metric(
                    "uninsured_percentile", "Uninsured percentile", detail.uninsured_percentile
                ),
                self._metric("uninsured_percent", "Uninsured percent", detail.uninsured_percent),
                self._metric("rucc_2023", "Rural-urban continuum code (2023)", detail.rucc_2023),
            ],
            data_completeness=self._metric(
                "evidence_completeness", "Evidence completeness", detail.evidence_completeness
            ),
        )

    def state_report(
        self,
        state: str,
        settings: ScoreSettings,
        dataset_version: str | None = None,
        template_version: str = "state-v1",
    ) -> StateReport:
        collection = self._atlas_service.scores(settings, dataset_version)
        counties = [county for county in collection.counties if county.state == state]
        if not counties:
            raise KeyError(state)
        metadata = self._atlas_service.metadata(dataset_version)
        state_name = counties[0].state_name
        return StateReport(
            identity=self._identity(template_version),
            geography=ReportGeography(
                level="state",
                identifier=state,
                name=state_name,
                state_code=state,
                state_name=state_name,
            ),
            provenance=self._provenance(metadata, settings),
            mean_score=self._mean_score(counties),
            summary=[
                self._metric("county_count", "Counties represented", len(counties)),
                self._metric(
                    "published_human_counties",
                    "Counties with published human surveillance records",
                    sum(county.human_status == "published_count_floor" for county in counties),
                ),
                self._metric(
                    "tick_record_counties",
                    "Counties with tick records",
                    sum(county.tick_status != "No records" for county in counties),
                ),
                self._metric(
                    "pathogen_record_counties",
                    "Counties with B. burgdorferi records",
                    sum(county.burgdorferi_status == "Present" for county in counties),
                ),
                self._metric(
                    "mean_evidence_completeness",
                    "Mean evidence completeness",
                    fmean(county.evidence_completeness for county in counties),
                ),
            ],
        )

    def _identity(self, template_version: str) -> ReportIdentity:
        return ReportIdentity(
            report_version=_REPORT_VERSION,
            template_version=template_version,
            generated_at=self._clock(),
        )

    @staticmethod
    def _provenance(metadata: AtlasMetadata, settings: ScoreSettings) -> ReportProvenance:
        return ReportProvenance(
            dataset_version=metadata.release_id,
            schema_version=metadata.schema_version,
            methodology_version=metadata.methodology_version,
            scoring_settings=settings.model_dump(),
            limitations=metadata.limitations,
            sources=[ReportSource(**source.model_dump()) for source in metadata.sources],
        )

    @staticmethod
    def _metric(key: str, label: str, value: int | float | str | None) -> ReportMetric:
        return ReportMetric(
            key=key,
            label=label,
            value=value,
            availability="available" if value is not None else "unavailable",
        )

    @staticmethod
    def _score(detail: CountyDetail) -> ReportScore:
        return ReportScore(**detail.score.model_dump())

    @staticmethod
    def _mean_score(counties: list[CountyScoreSummary]) -> ReportScore:
        return ReportScore(
            score=fmean(county.score.score for county in counties),
            human_weakness=fmean(county.score.human_weakness for county in counties),
            ecological=fmean(county.score.ecological for county in counties),
            community=fmean(county.score.community for county in counties),
            tick_signal=fmean(county.score.tick_signal for county in counties),
            pathogen_signal=fmean(county.score.pathogen_signal for county in counties),
            svi_signal=fmean(county.score.svi_signal for county in counties),
            access_signal=fmean(county.score.access_signal for county in counties),
            rural_signal=fmean(county.score.rural_signal for county in counties),
        )
