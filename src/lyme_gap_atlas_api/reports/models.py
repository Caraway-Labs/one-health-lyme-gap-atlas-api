"""Stable report data contracts kept separate from HTTP and renderer models."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from ..public_contract import Observation


class ReportIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_version: str
    template_version: str
    generated_at: datetime


class ReportGeography(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: Literal["county", "state"]
    identifier: str
    name: str
    state_code: str
    state_name: str


class ReportSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    vintage: str
    url: str
    note: str


class ReportProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_version: str
    schema_version: str
    methodology_version: str
    scoring_settings: dict[str, int]
    limitations: str
    sources: list[ReportSource]


class ReportMetric(BaseModel):
    """A value whose availability is explicit so missing data cannot become zero."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    value: int | float | str | None
    availability: Literal["available", "unavailable"]

    @model_validator(mode="after")
    def validate_availability(self) -> "ReportMetric":
        if self.availability == "available" and self.value is None:
            raise ValueError("available metrics require a value")
        if self.availability == "unavailable" and self.value is not None:
            raise ValueError("unavailable metrics must not carry a value")
        return self


class ReportScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: float
    human_weakness: float
    ecological: float
    community: float
    tick_signal: float
    pathogen_signal: float
    svi_signal: float
    access_signal: float
    rural_signal: float


class ReportObservationContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    period_start: date
    period_end: date
    observations: list[Observation]


class BriefRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: str
    label: str
    text: str


class BriefLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    url: str


class CountyDataBrief(BaseModel):
    """Published two-page County Data Brief. Recommendation text is server-owned."""

    model_config = ConfigDict(frozen=True)

    copy_version: str
    branding: str
    report_title: str
    county_heading: str
    response_title: str
    response_status: Literal["assigned", "unavailable"]
    score_label: str
    fips: str
    why_it_surfaced: str
    categories: list[BriefRow]
    interpretation: str
    posture: str
    workstreams: list[BriefRow]
    questions: list[BriefRow]
    resources: list[BriefLink]
    traceability: list[str]


class CountyReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: ReportIdentity
    geography: ReportGeography
    provenance: ReportProvenance
    score: ReportScore
    observation_context: ReportObservationContext | None = None
    priority: str
    population: ReportMetric
    human_health: list[ReportMetric]
    ecological: list[ReportMetric]
    data_completeness: ReportMetric
    brief: CountyDataBrief | None = None


class StateReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: ReportIdentity
    geography: ReportGeography
    provenance: ReportProvenance
    mean_score: ReportScore
    summary: list[ReportMetric]
