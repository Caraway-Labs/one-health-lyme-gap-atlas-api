"""Cached application service and server-authoritative scoring."""

import csv
import io
import threading
import time
from collections.abc import Callable
from typing import Any

from lyme_gap_atlas_shared.domain import (
    CountyInputs,
    ScoreSettings,
    normalize_county_fips,
    priority_label,
    score_color,
    score_county,
)

from .models import (
    AtlasMetadata,
    CountyDetail,
    CountyMapValues,
    CountyRecord,
    CountyScoreSummary,
    MapValuesCollection,
    ScoreCollection,
)
from .repository import AtlasDataUnavailableError, AtlasRepository, Snapshot
from .telemetry_logging import operational_logger

logger = operational_logger(__name__)


class AtlasService:
    """Serves one cached release snapshot.

    A refresh failure keeps serving the previous snapshot for at most
    ``max_stale_seconds`` past its TTL, refreshes run in a single flight, and failed
    refreshes back off so an outage is not retried once per request. The snapshot's
    own release_id/generated_at/last_updated are never rewritten; staleness is exposed
    to the HTTP layer only through ``is_stale`` (used to withhold public caching).
    """

    def __init__(
        self,
        repository: AtlasRepository,
        ttl_seconds: int = 300,
        *,
        max_stale_seconds: float = 900,
        refresh_backoff_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repository = repository
        self.ttl_seconds = ttl_seconds
        self.max_stale_seconds = max_stale_seconds
        self.refresh_backoff_seconds = refresh_backoff_seconds
        self._clock = clock
        self._snapshot: Snapshot | None = None
        self._loaded_at = 0.0
        self._next_refresh_at = 0.0
        self._degraded = False
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return self.repository.ready()

    @property
    def is_stale(self) -> bool:
        """True while the cached snapshot is older than its TTL (never claim it is fresh)."""
        return self._snapshot is not None and self._clock() - self._loaded_at >= self.ttl_seconds

    def snapshot(self) -> Snapshot:
        snapshot = self._snapshot
        if snapshot is not None and self._clock() - self._loaded_at < self.ttl_seconds:
            return snapshot
        with self._lock:
            now = self._clock()
            if self._snapshot is not None and now - self._loaded_at < self.ttl_seconds:
                return self._snapshot
            if now < self._next_refresh_at:
                return self._stale_or_unavailable(now, None)
            try:
                self._snapshot = self.repository.load_snapshot()
            except Exception as exc:
                self._next_refresh_at = now + self.refresh_backoff_seconds
                if not self._degraded:
                    self._degraded = True
                    logger.warning(
                        "atlas_snapshot_refresh_degraded",
                        extra={
                            "context": {
                                "has_snapshot": self._snapshot is not None,
                                "backoff_seconds": self.refresh_backoff_seconds,
                            }
                        },
                    )
                return self._stale_or_unavailable(now, exc)
            self._loaded_at = self._clock()
            self._next_refresh_at = 0.0
            if self._degraded:
                self._degraded = False
                logger.info("atlas_snapshot_refresh_recovered")
            return self._snapshot

    def _stale_or_unavailable(self, now: float, cause: Exception | None) -> Snapshot:
        """Serve the prior snapshot within the stale bound, else the existing 503 error."""
        if (
            self._snapshot is not None
            and now - self._loaded_at < self.ttl_seconds + self.max_stale_seconds
        ):
            return self._snapshot
        if isinstance(cause, AtlasDataUnavailableError):
            raise cause
        raise AtlasDataUnavailableError("Atlas data service is unavailable") from cause

    def metadata(self, dataset_version: str | None = None) -> AtlasMetadata:
        snapshot = self.snapshot()
        self._require_version(snapshot, dataset_version)
        return snapshot.metadata.model_copy(update={"score_defaults": ScoreSettings().model_dump()})

    def geometry(self, dataset_version: str | None = None) -> dict[str, Any]:
        """Return only governed SVI display GeoJSON, joined by canonical county FIPS.

        Analytical TIGER polygons belong to raster aggregation, never this resource.
        Keep the legacy GeoJSON shape and original display coordinates unchanged.
        """
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

    def map_values(self, dataset_version: str | None = None) -> MapValuesCollection:
        snapshot = self.snapshot()
        self._require_version(snapshot, dataset_version)
        counties = sorted(
            (
                CountyMapValues(
                    fips=record.fips,
                    incidence_floor_2023=record.incidence_floor_2023,
                    svi_percentile=record.svi_percentile,
                    uninsured_percent=record.uninsured_percent,
                    rucc_2023=record.rucc_2023,
                )
                for record in snapshot.counties
            ),
            key=lambda item: item.fips,
        )
        return MapValuesCollection(
            release_id=snapshot.metadata.release_id,
            counties=counties,
        )

    def county(
        self, fips: str, settings: ScoreSettings, dataset_version: str | None = None
    ) -> CountyDetail:
        # Keep the public path pattern and its 422 response in the REST adapter.
        # This invariant also applies to in-process callers of the service.
        fips = normalize_county_fips(fips)
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
            state_unallocated_records_2023=record.state_unallocated_records_2023,
            scapularis_status=record.scapularis_status,
            pacificus_status=record.pacificus_status,
            uninsured_percent=record.uninsured_percent,
            release=self.metadata(dataset_version),
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
                "scoring_methodology_version",
                "smooth_k",
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
                    item.scoring_methodology_version,
                    settings.smooth_k,
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
            svi_percentile=record.svi_percentile,
            uninsured_percentile=record.uninsured_percentile,
            rucc_2023=record.rucc_2023,
            human_status=record.human_status,
            incidence_floor_2023=record.incidence_floor_2023,
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
            "ecological": item.tick_status in {"Established", "Reported"}
            or item.burgdorferi_status == "Present",
            "human": item.human_status == "published_count_floor",
            "complete": item.evidence_completeness >= 5,
        }[evidence]

    @staticmethod
    def _require_version(snapshot: Snapshot, requested: str | None) -> None:
        if requested is not None and requested != snapshot.metadata.release_id:
            raise LookupError(requested)
