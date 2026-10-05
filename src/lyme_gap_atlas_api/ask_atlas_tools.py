"""Internal, bounded Ask Atlas structured tools (contract v1). No HTTP or SQL surface."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date
from pathlib import Path
from threading import Event, Lock
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .public_contract import (
    GeographyIdentity,
    GeographyType,
    Measure,
    Methodology,
    Observation,
    ObservationQuery,
    PublicQueryError,
    Source,
)
from .public_metadata import MetadataService
from .public_observations import ObservationService
from .public_provenance import ProvenanceService
from .repository import AtlasDataUnavailableError

CONTRACT_VERSION = "ask-atlas-tools-v1"
TOOL_DEADLINES = {"find_measures": 3.0, "get_observations": 8.0, "get_evidence_metadata": 3.0}
TOOL_SCHEMA = json.loads(Path(__file__).with_name("ask-atlas-tools-v1.schema.json").read_text())


class StrictInput(BaseModel):
    # JSON Schema checks wire types; Pydantic then parses ISO dates and enum strings.
    model_config = ConfigDict(extra="forbid")


class FindMeasuresInput(StrictInput):
    tool: Literal["find_measures"]
    indicator_id: str | None = Field(default=None, min_length=1, max_length=120)
    search_text: str | None = Field(default=None, min_length=1, max_length=120)
    page_size: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def one_selector(self) -> "FindMeasuresInput":
        if (self.indicator_id is None) == (self.search_text is None):
            raise ValueError("Supply exactly one measure selector")
        return self


class GetObservationsInput(StrictInput):
    tool: Literal["get_observations"]
    measure_id: str = Field(min_length=1, max_length=120)
    geography_type: GeographyType
    geography_ids: list[str] = Field(min_length=1, max_length=20)
    year: int | None = Field(default=None, ge=1900, le=2100)
    start_date: date | None = None
    end_date: date | None = None


class GetEvidenceMetadataInput(StrictInput):
    tool: Literal["get_evidence_metadata"]
    observation_ids: list[str] = Field(min_length=1, max_length=20)


class Coverage(BaseModel):
    geography: GeographyIdentity
    period_start: date
    period_end: date
    state: Literal["present", "absent"]
    observation_id: str | None
    coverage_id: str
    measure_id: str
    release_id: str


class MetadataItem(BaseModel):
    observation_id: str
    source: Source
    methodology: Methodology | None


class ToolResult(BaseModel):
    kind: Literal["tool_result"] = "tool_result"
    tool: Literal["find_measures", "get_observations", "get_evidence_metadata"]
    status: Literal["ok", "error"]
    release_id: str | None = None
    measures: list[Measure] = Field(default_factory=list, max_length=20)
    observations: list[Observation] = Field(default_factory=list, max_length=200)
    coverage: list[Coverage] = Field(default_factory=list, max_length=200)
    metadata: list[MetadataItem] = Field(default_factory=list, max_length=20)
    error_code: str | None = None


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _annual(value: str) -> bool:
    return value.casefold() in {"year", "annual"}


def _coverage_id(release: str, measure: str, geography: str, start: date, end: date) -> str:
    payload = json.dumps(
        [release, measure, geography, start.isoformat(), end.isoformat()],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "coverage:" + hashlib.sha256(payload.encode("ascii")).hexdigest()[:24]


class StructuredTools:
    """Request-local adapter; retain admitted observations only within this instance."""

    def __init__(
        self,
        metadata: MetadataService,
        observations: ObservationService,
        provenance: ProvenanceService,
    ) -> None:
        self.metadata_service = metadata
        self.observation_service = observations
        self.provenance_service = provenance
        self.pinned_release: str | None = None
        self.admitted: dict[str, Observation] = {}
        self.started = time.monotonic()
        self.expired = False
        self.cancelled = Event()
        self.state_lock = Lock()
        self.call_deadline = float("inf")

    def _check_active(self) -> None:
        if (
            self.cancelled.is_set()
            or time.monotonic() >= self.call_deadline
            or time.monotonic() - self.started >= 12
        ):
            raise TimeoutError

    def _expire(self) -> None:
        with self.state_lock:
            self.expired = True
            self.cancelled.set()
            self.admitted.clear()

    def _release(self) -> str:
        self._check_active()
        release = self.observation_service.repository.current_release()
        self._check_active()
        if self.pinned_release is not None and release != self.pinned_release:
            raise PublicQueryError(
                "RELEASE_CHANGED", "The governed release changed; retry the request."
            )
        self.pinned_release = release
        return release

    def _execute(
        self,
        tool: Literal["find_measures", "get_observations", "get_evidence_metadata"],
        model: type[StrictInput],
        raw: dict[str, Any],
    ) -> ToolResult:
        start = time.monotonic()
        dispatched = False
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(
            "atlas.ask_atlas.tool", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("atlas.tool.id", tool)
            span.set_attribute("atlas.tool.contract_version", CONTRACT_VERSION)
            try:
                if self.expired:
                    raise TimeoutError
                if not isinstance(raw, dict):
                    raise PublicQueryError("INVALID_REQUEST", "Tool input must be an object.")
                if tool != "get_evidence_metadata":
                    self.admitted.clear()
                bounded_field = {
                    "find_measures": ("page_size", 20),
                    "get_observations": ("geography_ids", 20),
                    "get_evidence_metadata": ("observation_ids", 20),
                }[tool]
                candidate = raw.get(bounded_field[0])
                if (isinstance(candidate, list) and len(candidate) > bounded_field[1]) or (
                    bounded_field[0] == "page_size"
                    and type(candidate) is int
                    and candidate > bounded_field[1]
                ):
                    raise PublicQueryError("QUERY_TOO_BROAD", "Tool input exceeds its ceiling.")
                # The reviewed API17 schema is the wire contract, not a parallel generated schema.
                errors = list(
                    Draft202012Validator(TOOL_SCHEMA, format_checker=FormatChecker()).iter_errors(
                        raw
                    )
                )
                if errors:
                    raise PublicQueryError("INVALID_REQUEST", "Invalid tool input.")
                args = model.model_validate(raw)
                if tool == "get_observations":
                    self._observation_query(GetObservationsInput.model_validate(args))
                elif tool == "find_measures" and not (
                    FindMeasuresInput.model_validate(args).indicator_id
                    or _normalized(FindMeasuresInput.model_validate(args).search_text or "")
                ):
                    raise PublicQueryError("INVALID_REQUEST", "Search text must contain a term.")
                elif tool == "get_evidence_metadata":
                    ids = GetEvidenceMetadataInput.model_validate(args).observation_ids
                    if len(set(ids)) != len(ids):
                        raise PublicQueryError("INVALID_REQUEST", "Duplicate observation ID.")
                    if any(identifier not in self.admitted for identifier in ids):
                        raise PublicQueryError(
                            "RESOURCE_NOT_FOUND", "Observation was not admitted."
                        )
                remaining = min(
                    TOOL_DEADLINES[tool] - (time.monotonic() - start),
                    12 - (time.monotonic() - self.started),
                )
                if remaining <= 0:
                    raise TimeoutError
                self.call_deadline = time.monotonic() + remaining
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="atlas-tool")
                try:
                    dispatched = True
                    future = executor.submit(copy_context().run, self._dispatch, tool, args)
                    result = future.result(timeout=remaining)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
                if (
                    time.monotonic() - start > TOOL_DEADLINES[tool]
                    or time.monotonic() - self.started > 12
                ):
                    raise TimeoutError
                if tool == "get_evidence_metadata":
                    self.admitted.clear()
                span.set_attribute("atlas.tool.outcome", "ok")
                span.set_attribute(
                    "atlas.tool.result_count",
                    len(result.measures) + len(result.observations) + len(result.metadata),
                )
                return result
            except (ValidationError, ValueError, TypeError) as exc:
                if isinstance(exc, PublicQueryError):
                    code = exc.code
                else:
                    code = "SOURCE_UNAVAILABLE" if dispatched else "INVALID_REQUEST"
            except TimeoutError:
                code = "SOURCE_UNAVAILABLE"
                self._expire()
            except AtlasDataUnavailableError:
                code = "SOURCE_UNAVAILABLE"
            except Exception:
                code = "SOURCE_UNAVAILABLE"
            span.set_attribute("atlas.tool.outcome", "error")
            span.set_attribute("atlas.tool.error_code", code)
            self.admitted.clear()
            return ToolResult(tool=tool, status="error", error_code=code)

    def _dispatch(
        self,
        tool: Literal["find_measures", "get_observations", "get_evidence_metadata"],
        args: StrictInput,
    ) -> ToolResult:
        self._check_active()
        if tool == "find_measures":
            result = self._find(args)
        elif tool == "get_observations":
            result = self._observations(args)
        else:
            result = self._metadata(args)
        self._release()
        self._check_active()
        if tool == "get_observations":
            with self.state_lock:
                self._check_active()
                self.admitted = {o.observation_id: o for o in result.observations}
        return result

    def find_measures(self, raw: dict[str, Any]) -> ToolResult:
        return self._execute("find_measures", FindMeasuresInput, raw)

    def get_observations(self, raw: dict[str, Any]) -> ToolResult:
        return self._execute("get_observations", GetObservationsInput, raw)

    def get_evidence_metadata(self, raw: dict[str, Any]) -> ToolResult:
        return self._execute("get_evidence_metadata", GetEvidenceMetadataInput, raw)

    def verify_release(self) -> str:
        """Check the pinned release immediately before an answer admits evidence."""
        if self.expired:
            raise TimeoutError
        return self._release()

    def _find(self, input: StrictInput) -> ToolResult:
        args = FindMeasuresInput.model_validate(input)
        release = self._release()
        self._check_active()
        indicators, measures = self.metadata_service.discover(checkpoint=self._check_active)
        self._check_active()
        if any(item.release_version != release for item in indicators) or any(
            item.release_version != release for item in measures
        ):
            raise PublicQueryError("RELEASE_CHANGED", "Metadata release changed.")
        if args.indicator_id is not None:
            if args.indicator_id not in {item.indicator_id for item in indicators}:
                raise PublicQueryError("RESOURCE_NOT_FOUND", "Unknown indicator.")
            matches = [m for m in measures if m.indicator_id == args.indicator_id]
        else:
            needle = _normalized(args.search_text or "")
            if not needle:
                raise PublicQueryError("INVALID_REQUEST", "Search text must contain a term.")
            matches = [
                m
                for m in measures
                if needle in _normalized(m.measure_id) or needle in _normalized(m.label)
            ]
            matches.sort(
                key=lambda m: (
                    0
                    if _normalized(m.measure_id) == needle
                    else 1
                    if _normalized(m.label) == needle
                    else 2,
                    m.measure_id,
                )
            )
        if not matches:
            raise PublicQueryError("RESOURCE_NOT_FOUND", "No governed measure matches.")
        if len(matches) > args.page_size or len(matches) > 20:
            raise PublicQueryError("QUERY_TOO_BROAD", "Narrow measure selection.")
        return ToolResult(tool="find_measures", status="ok", release_id=release, measures=matches)

    @staticmethod
    def _observation_query(args: GetObservationsInput) -> ObservationQuery:
        query = ObservationQuery(
            measure_id=args.measure_id,
            geography_type=args.geography_type,
            geography_id=args.geography_ids,
            year=args.year,
            start_date=args.start_date,
            end_date=args.end_date,
            page_size=200,
        )
        query.validate_bounds(ceiling=200, annual=True)
        if args.geography_type != GeographyType.county:
            raise PublicQueryError("UNSUPPORTED_FILTER", "Only county geography is supported.")
        if (
            args.year is None
            and args.start_date is not None
            and args.end_date is not None
            and (
                args.start_date.month,
                args.start_date.day,
                args.end_date.month,
                args.end_date.day,
            )
            != (1, 1, 12, 31)
        ):
            raise PublicQueryError("UNSUPPORTED_FILTER", "Only whole annual periods are supported.")
        return query

    def _observations(self, input: StrictInput) -> ToolResult:
        args = GetObservationsInput.model_validate(input)
        query = self._observation_query(args)
        release = self._release()
        self._check_active()
        indicators, measures = self.metadata_service.discover(checkpoint=self._check_active)
        self._check_active()
        if any(item.release_version != release for item in indicators) or any(
            item.release_version != release for item in measures
        ):
            raise PublicQueryError("RELEASE_CHANGED", "Metadata release changed.")
        measure = next((m for m in measures if m.measure_id == args.measure_id), None)
        if measure is None:
            raise PublicQueryError("RESOURCE_NOT_FOUND", "Unknown measure.")
        if (
            args.geography_type != GeographyType.county
            or (
                measure.geography_types is not None
                and args.geography_type not in measure.geography_types
            )
            or (
                measure.geography_types is None
                and measure.geography_semantics not in {"COUNTY", "COUNTY_FIPS_5"}
            )
            or (
                measure.temporal_grains is not None
                and not any(_annual(grain) for grain in measure.temporal_grains)
            )
            or (
                measure.temporal_grains is None
                and (
                    measure.temporal_semantics is None
                    or not (
                        _annual(measure.temporal_semantics)
                        or (
                            measure.temporal_semantics.isdigit()
                            and len(measure.temporal_semantics) == 4
                        )
                    )
                )
            )
            or (
                measure.measure_type is not None
                and measure.measure_type.upper() in {"PREDICTED", "PREDICTION", "MODEL_OUTPUT"}
            )
        ):
            raise PublicQueryError(
                "UNSUPPORTED_FILTER", "Unsupported governed geography or period."
            )
        if args.year is not None:
            years = [args.year]
        elif args.start_date is not None and args.end_date is not None:
            years = list(range(args.start_date.year, args.end_date.year + 1))
        else:
            raise PublicQueryError("INVALID_REQUEST", "Complete period required.")
        if (
            measure.temporal_semantics is not None
            and measure.temporal_semantics.isdigit()
            and any(year != int(measure.temporal_semantics) for year in years)
        ):
            raise PublicQueryError("UNSUPPORTED_FILTER", "Requested year is outside measure scope.")
        self._check_active()
        try:
            envelope = self.observation_service.search(query, checkpoint=self._check_active)
        except Exception:
            self._release()
            raise
        self._release()
        if envelope.meta.next_page_token is not None:
            raise PublicQueryError("QUERY_TOO_BROAD", "Result exceeds tool ceiling.")
        slots: dict[tuple[str, int], Observation | None] = {
            (geo, year): None for geo in args.geography_ids for year in years
        }
        seen_ids: set[str] = set()
        for observation in envelope.data:
            key = (observation.geography.geography_id, observation.period_start.year)
            if (
                key not in slots
                or slots[key] is not None
                or observation.observation_id in seen_ids
                or observation.release_id != release
                or observation.measure_id != args.measure_id
                or not _annual(observation.temporal_grain)
                or observation.period_start != date(key[1], 1, 1)
                or observation.period_end != date(key[1], 12, 31)
                or observation.unit != measure.unit
                or observation.strata
                or observation.evidence.resource_type != "observation"
                or observation.evidence.resource_id != observation.observation_id
                or observation.evidence.release_id != release
                or observation.evidence.source_id != observation.source_id
                or observation.evidence.provenance_ref != observation.provenance_ref
                or observation.evidence.semantic_version != observation.semantic_version
                or observation.evidence.methodology_version != observation.methodology_version
            ):
                raise AtlasDataUnavailableError("Inconsistent governed observation.")
            slots[key] = observation
            seen_ids.add(observation.observation_id)
        coverage = [
            Coverage(
                geography=GeographyIdentity(geography_type=args.geography_type, geography_id=geo),
                period_start=date(year, 1, 1),
                period_end=date(year, 12, 31),
                state="present" if obs else "absent",
                observation_id=obs.observation_id if obs else None,
                coverage_id=_coverage_id(
                    release, args.measure_id, geo, date(year, 1, 1), date(year, 12, 31)
                ),
                measure_id=args.measure_id,
                release_id=release,
            )
            for (geo, year), obs in slots.items()
        ]
        return ToolResult(
            tool="get_observations",
            status="ok",
            release_id=release,
            observations=envelope.data,
            coverage=coverage,
        )

    def _metadata(self, input: StrictInput) -> ToolResult:
        args = GetEvidenceMetadataInput.model_validate(input)
        release = self._release()
        if len(set(args.observation_ids)) != len(args.observation_ids):
            raise PublicQueryError("INVALID_REQUEST", "Duplicate observation ID.")
        if any(identifier not in self.admitted for identifier in args.observation_ids):
            raise PublicQueryError("RESOURCE_NOT_FOUND", "Observation was not admitted.")
        items = []
        for identifier in args.observation_ids:
            self._check_active()
            observation = self.admitted[identifier]
            if observation.release_id != release:
                raise PublicQueryError("RELEASE_CHANGED", "Observation release changed.")
            try:
                self._check_active()
                source = self.provenance_service.source(
                    observation.source_id, checkpoint=self._check_active
                )
                self._check_active()
                method = (
                    self.provenance_service.methodology(
                        observation.methodology_id, checkpoint=self._check_active
                    )
                    if observation.methodology_id
                    else None
                )
                self._check_active()
            except PublicQueryError as exc:
                if exc.code == "RESOURCE_NOT_FOUND":
                    raise AtlasDataUnavailableError("Governed provenance is unresolved") from exc
                raise
            if (
                source.source_id != observation.source_id
                or source.release_version != release
                or (
                    method is not None
                    and (
                        method.methodology_id != observation.methodology_id
                        or method.measure_id != observation.measure_id
                        or method.release_version != release
                    )
                )
            ):
                raise PublicQueryError("RELEASE_CHANGED", "Provenance release changed.")
            items.append(MetadataItem(observation_id=identifier, source=source, methodology=method))
        return ToolResult(
            tool="get_evidence_metadata", status="ok", release_id=release, metadata=items
        )
