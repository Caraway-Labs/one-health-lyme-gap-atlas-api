from datetime import datetime
from typing import Any, Literal

from lyme_gap_atlas_shared import Score
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ChatHistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=5_000)


class KnowledgeChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1_000)
    conversation_id: str | None = Field(default=None, max_length=100)
    conversation_token: str | None = Field(default=None, max_length=200)
    history: list[ChatHistoryTurn] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def bounded_history(self) -> "KnowledgeChatRequest":
        if sum(len(turn.content) for turn in self.history) > 30_000:
            raise ValueError("history exceeds 30,000 characters")
        if self.history and (self.conversation_id and self.conversation_token):
            raise ValueError("history is accepted only for a new or non-persisted conversation")
        for index, turn in enumerate(self.history):
            expected = "user" if index % 2 == 0 else "assistant"
            if turn.role != expected:
                raise ValueError("history must contain alternating user/assistant pairs")
        if self.history and len(self.history) % 2:
            raise ValueError("history must contain complete user/assistant pairs")
        return self


class KnowledgeClaim(BaseModel):
    claim_id: str
    text: str
    citation_ids: list[str] = Field(min_length=1)


class KnowledgeCitation(BaseModel):
    citation_id: str
    pmid: str = Field(pattern=r"^\d{1,10}$")
    title: str
    pubmed_url: str
    claim_ids: list[str] = Field(min_length=1)
    passage_ids: list[str] = Field(min_length=1)
    source_label: str = "PubMed / PMC Open Access"


class KnowledgeChatResponse(BaseModel):
    request_id: str
    conversation_id: str
    conversation_token: str | None = None
    configuration_version: str
    status: Literal[
        "answered",
        "no_evidence",
        "evidence_unavailable",
        "safety_refusal",
        "capacity_limited",
    ]
    answer: str
    claims: list[KnowledgeClaim] = Field(default_factory=list)
    citations: list[KnowledgeCitation] = Field(default_factory=list)
