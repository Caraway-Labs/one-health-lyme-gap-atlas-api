"""Cached application service and server-authoritative scoring."""

import csv
import io
import threading
import time
from typing import Any

from lyme_gap_atlas_shared import (
    CountyInputs,
    ScoreSettings,
    priority_label,
    score_color,
    score_county,
)

from .models import AtlasMetadata, CountyDetail, CountyRecord, CountyScoreSummary, ScoreCollection
from .repository import AtlasRepository, Snapshot


class AtlasService:
    def __init__(self, repository: AtlasRepository, ttl_seconds: int = 300) -> None:
        self.repository = repository
        self.ttl_seconds = ttl_seconds
        self._snapshot: Snapshot | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return self.repository.ready()

    def snapshot(self) -> Snapshot:
        if self._snapshot is not None and time.monotonic() - self._loaded_at < self.ttl_seconds:
            return self._snapshot
        with self._lock:
            if self._snapshot is None or time.monotonic() - self._loaded_at >= self.ttl_seconds:
                self._snapshot = self.repository.load_snapshot()
                self._loaded_at = time.monotonic()
        return self._snapshot

    def metadata(self, dataset_version: str | None = None) -> AtlasMetadata:
        snapshot = self.snapshot()
        self._require_version(snapshot, dataset_version)
        return snapshot.metadata

    def geometry(self, dataset_version: str | None = None) -> dict[str, Any]:
        snapshot = self.snapshot()
        self._require_version(snapshot, dataset_version)
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": county.fips,
                    "properties": {"fips": county.fips},
                    "geometry": county.geometry,
                }
                for county in snapshot.counties
            ],
        }

    def scores(
        self, settings: ScoreSettings, dataset_version: str | None = None
    ) -> ScoreCollection:
        snapshot = self.snapshot()
        self._require_version(snapshot, dataset_version)
        counties = [self._summary(county, settings) for county in snapshot.counties]
        counties.sort(key=lambda item: (-item.score.score, item.fips))
        return ScoreCollection(
            release_id=snapshot.metadata.release_id,
            methodology_version=snapshot.metadata.methodology_version,
            settings=settings.model_dump(),
            counties=counties,
        )

    def county(
        self, fips: str, settings: ScoreSettings, dataset_version: str | None = None
    ) -> CountyDetail:
        snapshot = self.snapshot()
        self._require_version(snapshot, dataset_version)
        record = next((item for item in snapshot.counties if item.fips == fips), None)
        if record is None:
            raise KeyError(fips)
        summary = self._summary(record, settings)
        return CountyDetail(
            **summary.model_dump(),
            population=record.population,
            case_count_floor_2023=record.case_count_floor_2023,
            incidence_floor_2023=record.incidence_floor_2023,
            state_unallocated_records_2023=record.state_unallocated_records_2023,
            scapularis_status=record.scapularis_status,
            pacificus_status=record.pacificus_status,
            svi_percentile=record.svi_percentile,
            uninsured_percentile=record.uninsured_percentile,
            uninsured_percent=record.uninsured_percent,
            rucc_2023=record.rucc_2023,
            release=snapshot.metadata,
        )

    def ranking_csv(self, settings: ScoreSettings, state: str, query: str, evidence: str) -> str:
        summaries = self.scores(settings).counties
        filtered = [item for item in summaries if self._matches(item, state, query, evidence)]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "rank",
                "fips",
                "county",
                "state",
                "score",
                "priority",
                "human_status",
                "tick_status",
                "burgdorferi_status",
                "evidence_completeness",
            ]
        )
        for rank, item in enumerate(filtered, 1):
            writer.writerow(
                [
                    rank,
                    item.fips,
                    item.county,
                    item.state,
                    item.score.score,
                    item.priority,
                    item.human_status,
                    item.tick_status,
                    item.burgdorferi_status,
                    item.evidence_completeness,
                ]
            )
        return output.getvalue()

    @staticmethod
    def _summary(record: CountyRecord, settings: ScoreSettings) -> CountyScoreSummary:
        score = score_county(CountyInputs(**record.model_dump()), settings)
        return CountyScoreSummary(
            fips=record.fips,
            county=record.county,
            state=record.state,
            state_name=record.state_name,
            in_contiguous_tick_scope=record.in_contiguous_tick_scope,
            human_status=record.human_status,
            tick_status=record.tick_status,
            burgdorferi_status=record.burgdorferi_status,
            evidence_completeness=record.evidence_completeness,
            score=score,
            priority=priority_label(score.score),
            color=score_color(score.score, record.in_contiguous_tick_scope),
        )

    @staticmethod
    def _matches(item: CountyScoreSummary, state: str, query: str, evidence: str) -> bool:
        if state != "ALL" and item.state != state:
            return False
        needle = query.strip().lower()
        if needle and needle not in f"{item.county} {item.state} {item.fips}".lower():
            return False
        return {
            "all": True,
            "ecological": item.tick_status != "No records" or item.burgdorferi_status == "Present",
            "human": item.human_status == "published_count_floor",
            "complete": item.evidence_completeness >= 5,
        }[evidence]

    @staticmethod
    def _require_version(snapshot: Snapshot, requested: str | None) -> None:
        if requested is not None and requested != snapshot.metadata.release_id:
            raise LookupError(requested)
