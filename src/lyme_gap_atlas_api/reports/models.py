"""Stable report data contracts kept separate from HTTP and renderer models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


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


class CountyReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: ReportIdentity
    geography: ReportGeography
    provenance: ReportProvenance
    score: ReportScore
    priority: str
    population: ReportMetric
    human_health: list[ReportMetric]
    ecological: list[ReportMetric]
    data_completeness: ReportMetric


class StateReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: ReportIdentity
    geography: ReportGeography
    provenance: ReportProvenance
    mean_score: ReportScore
    summary: list[ReportMetric]
