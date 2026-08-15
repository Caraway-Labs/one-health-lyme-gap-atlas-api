from datetime import datetime
from typing import Any, Literal

from lyme_gap_atlas_shared import Score
from pydantic import BaseModel, ConfigDict, Field


class SourceMetadata(BaseModel):
    key: str
    label: str
    vintage: str
    url: str
    note: str


class AtlasMetadata(BaseModel):
    release_id: str
    schema_version: str
    generated_at: datetime
    loaded_at: datetime
    scope: str
    bundle_sha256: str
    score_defaults: dict[str, Any]
    methodology_version: str
    limitations: str
    sources: list[SourceMetadata]
    states: list[dict[str, str]]


class CountyRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    release_id: str
    fips: str = Field(pattern=r"^\d{5}$")
    county: str
    state: str
    state_name: str
    population: int | None
    in_contiguous_tick_scope: bool
    human_status: Literal["published_count_floor", "no_county_linked_record"]
    case_count_floor_2023: int | None
    incidence_floor_2023: float | None
    state_unallocated_records_2023: int | None
    tick_status: Literal["Established", "Reported", "No records"]
    scapularis_status: str | None
    pacificus_status: str | None
    burgdorferi_status: Literal["Present", "No records"]
    svi_percentile: float | None
    uninsured_percentile: float | None
    uninsured_percent: float | None
    rucc_2023: int | None
    evidence_completeness: int
    geometry: dict[str, Any]


class CountyScoreSummary(BaseModel):
    fips: str
    county: str
    state: str
    state_name: str
    in_contiguous_tick_scope: bool
    human_status: str
    tick_status: str
    burgdorferi_status: str
    evidence_completeness: int
    score: Score
    priority: str
    color: str


class ScoreCollection(BaseModel):
    release_id: str
    methodology_version: str
    settings: dict[str, int]
    counties: list[CountyScoreSummary]


class CountyDetail(CountyScoreSummary):
    population: int | None
    case_count_floor_2023: int | None
    incidence_floor_2023: float | None
    state_unallocated_records_2023: int | None
    scapularis_status: str | None
    pacificus_status: str | None
    svi_percentile: float | None
    uninsured_percentile: float | None
    uninsured_percent: float | None
    rucc_2023: int | None
    release: AtlasMetadata


class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str
    errors: list[dict[str, Any]] | None = None
