import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from lyme_gap_atlas_shared.domain import Score
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

ProfileRole = Literal[
    "general_public_citizen",
    "district_level_epidemiologist",
    "state_level_epidemiologist",
    "state_director_level_epidemiologist",
    "national_level_epidemiologist",
]

US_STATE_CODES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}


# Onboarding completion marker assigned by the server. Not a row revision.
STARTING_VIEW_VERSION: Literal[1] = 1

_COMPLETE_STARTING_VIEW_DESCRIPTION = (
    "Explicit onboarding-completion action. When supplied, the value must be JSON "
    "boolean true. That validates state_code and stores starting_view_version 1 "
    "with that jurisdiction in the same write. Omit this field on an ordinary "
    "Settings or profile save so the stored marker is left unchanged. Null, false, "
    "numbers, and strings are rejected. Clients cannot choose the version."
)


def _boolean_true_schema(schema: dict[str, Any]) -> None:
    """Publish boolean true only. Omission stays a Python default, not JSON null."""

    schema.pop("default", None)


_STARTING_VIEW_VERSION_DESCRIPTION = (
    "Server-owned starting-view onboarding marker. Null means this profile has "
    "not completed onboarding, including every profile saved before the marker "
    "existed. 1 means onboarding was completed with the stored state_code; a "
    "null state_code is United States. A later read that shows 1 and the "
    "requested state_code confirms that completion write. This is not a profile "
    "revision or an idempotency key."
)


def _normalize_state_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in US_STATE_CODES:
        raise ValueError("state_code must be a supported US state or DC")
    return normalized


def _normalize_bounded_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


class UserProfileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ProfileRole | None = None
    state_code: str | None = Field(default=None, max_length=2)
    organization: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    complete_starting_view: Literal[True] | SkipJsonSchema[None] = Field(
        default=None,
        description=_COMPLETE_STARTING_VIEW_DESCRIPTION,
        json_schema_extra=_boolean_true_schema,
    )

    @field_validator("complete_starting_view", mode="before")
    @classmethod
    def complete_starting_view_must_be_boolean_true(cls, value: object) -> object:
        if value is True:
            return True
        raise ValueError("complete_starting_view must be boolean true when supplied")

    @field_validator("state_code")
    @classmethod
    def validate_state_code(cls, value: str | None) -> str | None:
        return _normalize_state_code(value)

    @field_validator("organization", "job_title")
    @classmethod
    def normalize_bounded_text(cls, value: str | None) -> str | None:
        return _normalize_bounded_text(value)


class UserProfile(BaseModel):
    # Database adapter responses include server-owned columns such as user_id and
    # timestamps. They are deliberately not part of the browser-facing profile.
    # starting_view_version is the exception: it is server-owned and readable.
    model_config = ConfigDict(extra="ignore")

    role: ProfileRole | None = None
    state_code: str | None = Field(default=None, max_length=2)
    organization: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    starting_view_version: Literal[1] | None = Field(
        default=None,
        description=_STARTING_VIEW_VERSION_DESCRIPTION,
    )

    @field_validator("state_code")
    @classmethod
    def validate_state_code(cls, value: str | None) -> str | None:
        return _normalize_state_code(value)

    @field_validator("organization", "job_title")
    @classmethod
    def normalize_bounded_text(cls, value: str | None) -> str | None:
        return _normalize_bounded_text(value)


class UserProfileResponse(BaseModel):
    profile: UserProfile | None


PrivacyAction = Literal["export", "deletion"]
PrivacyRequestState = Literal[
    "requested",
    "verified",
    "confirmed",
    "in_progress",
    "completed",
    "needs_support",
]
PrivacyOmissionReason = Literal["not_connected", "not_account_linked", "browser_only"]


class PrivacyOmission(BaseModel):
    system: str
    reason: PrivacyOmissionReason
    detail: str


class PrivacyRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: PrivacyAction


class PrivacyRequestConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nonce: str = Field(min_length=16, max_length=128)


class PrivacyRequestStatus(BaseModel):
    request_id: str
    action: PrivacyAction
    state: PrivacyRequestState
    created_at: datetime
    completed_at: datetime | None = None
    download_available: bool = False
    omissions: list[PrivacyOmission] = Field(default_factory=list)
    support_reason: str | None = None
    confirmation_expires_at: datetime | None = None


class PrivacyRequestCreated(PrivacyRequestStatus):
    confirmation_nonce: str


class UserDataExportEnvelope(BaseModel):
    schema_version: Literal["atlas-user-data-export/v1"]
    generated_at: datetime
    request_id: str
    subject: dict[str, str]
    sources: list[dict[str, str]]
    data: dict[str, Any]
    omissions: list[PrivacyOmission]


_SCORING_VERSION_DESCRIPTION = (
    "Version of the Surveillance Gap Score method that produced the score. "
    "Scores from different versions are not comparable."
)
_PERCENTILE_NOTE = "Value from 0 to 1. `null` when none is published; `null` is not zero."
_RUCC_DESCRIPTION = (
    "2023 Rural-Urban Continuum Code, from 1 (most urban) to 9 (most rural). "
    "`null` when none is published."
)
_SVI_DESCRIPTION = "Social Vulnerability Index percentile of the county. " + _PERCENTILE_NOTE
_UNINSURED_PERCENTILE_DESCRIPTION = (
    "Percentile of the county's uninsured share among counties. " + _PERCENTILE_NOTE
)
_RELEASE_ID_DESCRIPTION = (
    "Identifies the published data release. Results from different releases are not "
    "directly comparable."
)
_METHODOLOGY_VERSION_DESCRIPTION = "Version of the release methodology that the scores follow."


class SourceMetadata(BaseModel):
    """A source dataset listed in the release metadata."""

    key: str = Field(description="Stable key of the source within this release.")
    label: str = Field(description="Display name of the source.")
    vintage: str = Field(description="Reference year or period of the source dataset.")
    url: str = Field(description="Web address of the source.")
    note: str = Field(description="How Atlas uses the source and any limit on that use.")


class AtlasMetadata(BaseModel):
    """Describes the published data release behind the scores and county records."""

    scoring_methodology_version: Literal["surveillance-gap-score-v1.1"] = Field(
        default="surveillance-gap-score-v1.1", description=_SCORING_VERSION_DESCRIPTION
    )
    release_id: str = Field(description=_RELEASE_ID_DESCRIPTION)
    schema_version: str = Field(description="Version of the release data layout.")
    generated_at: datetime = Field(
        description="When the release was built, with a UTC offset. It is not when the "
        "publishers last updated their data."
    )
    loaded_at: datetime = Field(
        description="When Atlas loaded the release for serving, with a UTC offset. It is not "
        "when the publishers last updated their data."
    )
    scope: str = Field(description="Geographic and time coverage of the release.")
    bundle_sha256: str = Field(
        description="SHA-256 checksum of the release contents. Use it to confirm you are "
        "looking at the same release."
    )
    score_defaults: dict[str, Any] = Field(
        description="Default settings the score uses when a request supplies none."
    )
    methodology_version: str = Field(description=_METHODOLOGY_VERSION_DESCRIPTION)
    limitations: str = Field(
        description="What the release does not show. Read it before you use a score."
    )
    sources: list[SourceMetadata] = Field(description="Source datasets used in the release.")
    states: list[dict[str, str]] = Field(
        description="States and the District of Columbia in the release. Each entry has a "
        "`code` and a `name`."
    )


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
    tick_status: Literal["Established", "Reported", "No records", "Unknown"]
    scapularis_status: str | None
    pacificus_status: str | None
    burgdorferi_status: Literal["Present", "No records", "Unknown"]
    svi_percentile: float | None
    uninsured_percentile: float | None
    uninsured_percent: float | None
    rucc_2023: int | None
    evidence_completeness: int
    geometry: dict[str, Any]


class CountyScoreSummary(BaseModel):
    """A county's Surveillance Gap Score with the evidence signals behind it.

    The score is a review aid. Atlas results are not a diagnosis. Atlas results are not an
    estimate of an individual's risk.
    """

    scoring_methodology_version: Literal["surveillance-gap-score-v1.1"] = Field(
        default="surveillance-gap-score-v1.1", description=_SCORING_VERSION_DESCRIPTION
    )
    incidence_floor_2023: float | None = Field(
        default=None,
        ge=0,
        description="Published 2023 minimum reported incidence per 100,000 people. "
        "A floor is a minimum, not a complete count. `null` is not zero.",
    )
    svi_percentile: float | None = Field(default=None, description=_SVI_DESCRIPTION)
    uninsured_percentile: float | None = Field(
        default=None, description=_UNINSURED_PERCENTILE_DESCRIPTION
    )
    rucc_2023: int | None = Field(default=None, description=_RUCC_DESCRIPTION)
    fips: str = Field(description="Five-digit county FIPS code, with leading zeros.")
    county: str = Field(description="County name.")
    state: str = Field(description="Two-letter state or District of Columbia code.")
    state_name: str = Field(description="Full state name.")
    in_contiguous_tick_scope: bool = Field(
        description="Whether the county is inside the contiguous area the tick data covers."
    )
    human_status: str = Field(
        description="State of the human-case data for the county. `published_count_floor` means "
        "a published case-count floor exists. `no_county_linked_record` means no record is "
        "linked to the county; it is neither null nor zero."
    )
    tick_status: str = Field(
        description="Tick evidence status the publisher reports: `Established`, `Reported`, "
        "`No records`, or `Unknown`. `No records` and `Unknown` do not establish absence."
    )
    burgdorferi_status: str = Field(
        description="Evidence status for the Lyme disease bacterium Borrelia burgdorferi: "
        "`Present`, `No records`, or `Unknown`. `No records` and `Unknown` do not establish "
        "absence."
    )
    evidence_completeness: int = Field(
        description="Whole-number measure of how much supporting evidence the county has. "
        "A higher number means more evidence is present. It does not rate evidence quality."
    )
    score: Score = Field(description="The score and the signals it combines.")
    priority: str = Field(
        description="Review-priority label for the score band. A lower label is not a "
        "safety finding."
    )
    color: str = Field(description="Hex color for the score band, for use in a map or legend.")


class ScoreCollection(BaseModel):
    """Surveillance Gap Scores for every county in the release, for one set of settings."""

    scoring_methodology_version: Literal["surveillance-gap-score-v1.1"] = Field(
        default="surveillance-gap-score-v1.1", description=_SCORING_VERSION_DESCRIPTION
    )
    release_id: str = Field(description=_RELEASE_ID_DESCRIPTION)
    methodology_version: str = Field(description=_METHODOLOGY_VERSION_DESCRIPTION)
    settings: dict[str, int] = Field(
        description="Score settings used for this response, such as `ecological_share`, "
        "`smooth_k`, and `missing_human_weakness`."
    )
    counties: list[CountyScoreSummary] = Field(description="One entry for each county.")


class CountyMapValues(BaseModel):
    """The county values a map needs to color its shapes, without the full score record."""

    fips: str = Field(
        pattern=r"^\d{5}$", description="Five-digit county FIPS code, with leading zeros."
    )
    incidence_floor_2023: float | None = Field(
        default=None,
        ge=0,
        description="Published 2023 minimum reported incidence per 100,000 people. "
        "A floor is a minimum, not a complete count. `null` is not zero.",
    )
    svi_percentile: float | None = Field(default=None, description=_SVI_DESCRIPTION)
    uninsured_percent: float | None = Field(
        default=None,
        description="Share of the county population without health insurance, as a percent. "
        "`null` when none is published.",
    )
    rucc_2023: int | None = Field(default=None, description=_RUCC_DESCRIPTION)


class MapValuesCollection(BaseModel):
    """County values for drawing a map. Show the same values in a table for non-map users."""

    release_id: str = Field(description=_RELEASE_ID_DESCRIPTION)
    counties: list[CountyMapValues] = Field(description="One entry for each county.")


class CountyDetail(CountyScoreSummary):
    """A county's score plus the release facts behind it."""

    population: int | None = Field(description="County population. `null` when none is published.")
    case_count_floor_2023: int | None = Field(
        description="Published 2023 minimum count of reported human cases. A floor is a "
        "minimum, not a complete count. `null` is not zero."
    )
    incidence_floor_2023: float | None = Field(
        description="Published 2023 minimum reported incidence per 100,000 people. "
        "`null` is not zero."
    )
    state_unallocated_records_2023: int | None = Field(
        description="2023 state-level records the publisher did not assign to a county. "
        "The count belongs to the state, not this county. `null` when none is published."
    )
    scapularis_status: str | None = Field(
        description="Publisher status for the tick Ixodes scapularis. `null` when none is "
        "published."
    )
    pacificus_status: str | None = Field(
        description="Publisher status for the tick Ixodes pacificus. `null` when none is published."
    )
    svi_percentile: float | None = Field(description=_SVI_DESCRIPTION)
    uninsured_percentile: float | None = Field(description=_UNINSURED_PERCENTILE_DESCRIPTION)
    uninsured_percent: float | None = Field(
        description="Share of the county population without health insurance, as a percent. "
        "`null` when none is published."
    )
    rucc_2023: int | None = Field(description=_RUCC_DESCRIPTION)
    release: AtlasMetadata = Field(description="The data release this record came from.")


class ProblemDetails(BaseModel):
    """RFC 9457 error body (`application/problem+json`). Some errors add a stable `code`."""

    type: str = Field(
        description="URI that names the kind of problem, such as `.../problems/validation`. "
        "Branch on `code` when it is present."
    )
    title: str = Field(
        description="Short summary of the kind of problem. Read `detail` for this occurrence."
    )
    status: int = Field(description="HTTP status code of the response, repeated in the body.")
    detail: str = Field(
        description="Explanation of this occurrence of the problem, in English. "
        "Show it to a developer; do not match on its wording."
    )
    instance: str = Field(
        description="Path of the request that failed, for example `/v1/observations`."
    )
    request_id: str = Field(
        description="Identifies this request. Include it when you contact support. "
        "It matches the `X-Request-ID` response header."
    )
    code: str | None = Field(
        default=None,
        description="Stable machine-readable error code: `INVALID_REQUEST`, "
        "`UNSUPPORTED_FILTER`, `UNSUPPORTED_STRATIFICATION`, `QUERY_TOO_BROAD`, "
        "`RESOURCE_NOT_FOUND`, `RATE_LIMITED`, or `SERVICE_UNAVAILABLE`. "
        "Branch on this field. It is absent on some errors, such as HTTP 422 validation errors.",
    )
    errors: list[dict[str, Any]] | None = Field(
        default=None,
        description="One entry for each invalid input when a request fails validation. "
        "Each entry has `loc` (where the value was sent), `msg`, and `type`. "
        "`null` or absent when the error is not about specific inputs.",
    )


FeedbackCategory = Literal["data_issue", "usability", "bug", "feature_idea", "general"]
FeedbackRouteId = Literal[
    "overview",
    "geographic_explorer",
    "evidence_library",
    "assistant",
    "account",
    "privacy",
    "ai_ethics",
]
FeedbackEvidenceView = Literal["all", "ecological", "human", "complete"]
FeedbackExplorerView = Literal[
    "tiles",
    "multiples",
    "matrix",
    "ranking",
    "maps",
    "scatter",
    "compare",
    "trends",
]
FeedbackExplorerMetric = Literal["score", "completeness"]

_FEEDBACK_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,64}$"
_FEEDBACK_DATASET_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
_FEEDBACK_APP_VERSION_PATTERN = r"^atlas-web/[A-Za-z0-9._-]{1,32}$"
_FIPS_PATTERN = r"^\d{5}$"


class FeedbackContext(BaseModel):
    """Closed geography and view context for a feedback submission."""

    model_config = ConfigDict(extra="forbid")

    state: str | None = None
    county_fips: str | None = Field(default=None, pattern=_FIPS_PATTERN)
    compare_county_fips: str | None = Field(default=None, pattern=_FIPS_PATTERN)
    selected_county_fips: list[str] | None = Field(default=None, max_length=5)
    dataset: str | None = Field(default=None, max_length=64, pattern=_FEEDBACK_DATASET_PATTERN)
    evidence_view: FeedbackEvidenceView | None = None
    explorer_view: FeedbackExplorerView | None = None
    explorer_metric: FeedbackExplorerMetric | None = None
    ecological_share: int | None = Field(default=None, ge=40, le=85, multiple_of=5)
    low_incidence_breakpoint: int | None = Field(default=None, ge=5, le=25)
    smooth_k: int | None = Field(default=None, ge=5, le=200)
    missing_human_weakness: int | None = Field(default=None, ge=40, le=90, multiple_of=5)
    source_ids: list[str] | None = Field(default=None, max_length=8)
    evidence_item_ids: list[str] | None = Field(default=None, max_length=8)
    item_ids: list[str] | None = Field(default=None, max_length=8)

    @field_validator("state")
    @classmethod
    def validate_state(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized == "ALL":
            return normalized
        if normalized not in US_STATE_CODES:
            raise ValueError("state must be ALL or a supported US state or DC")
        return normalized

    @field_validator("selected_county_fips")
    @classmethod
    def validate_selected_county_fips(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) != len(set(value)):
            raise ValueError("selected_county_fips must contain distinct FIPS codes")
        for item in value:
            if not re.fullmatch(_FIPS_PATTERN, item):
                raise ValueError("selected_county_fips entries must be five-digit FIPS codes")
        return value

    @field_validator("source_ids", "evidence_item_ids", "item_ids")
    @classmethod
    def validate_id_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        for item in value:
            if not re.fullmatch(_FEEDBACK_ID_PATTERN, item):
                raise ValueError("identifier lists must use 1–64 characters of [A-Za-z0-9._:-]")
        return value


class FeedbackSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_token: UUID
    category: FeedbackCategory
    message: str = Field(min_length=10, max_length=2000)
    contact_email: str | None = Field(default=None, max_length=254)
    route_id: FeedbackRouteId
    context: FeedbackContext | None = None
    app_version: str = Field(pattern=_FEEDBACK_APP_VERSION_PATTERN)

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("contact_email")
    @classmethod
    def validate_contact_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if " " in normalized or normalized.count("@") != 1:
            raise ValueError("contact_email must contain exactly one @ and no spaces")
        local, _, domain = normalized.partition("@")
        if not local or not domain:
            raise ValueError("contact_email must contain a local and domain part")
        if len(normalized) > 254:
            raise ValueError("contact_email must be at most 254 characters")
        return normalized


class FeedbackSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_id: UUID
    received_at: datetime
    replayed: bool


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
    # Optional Snowflake retrieval-corpus enrichment (Neo4j remains the evidence gate).
    pmcid: str | None = None
    corpus_unit_ids: list[str] | None = None
    section_labels: list[str] | None = None
    corpus_rules_version: str | None = None
    artifact_id: str | None = None
    contribution_sha256: str | None = None
    jats_sha256: str | None = None


EvidenceState = Literal[
    "single_study",
    "consistent",
    "limited",
    "mixed",
    "conflicting",
    "insufficient_to_compare",
    "no_relevant_corpus_evidence",
    "evidence_unavailable",
    "not_applicable",
]
SourceUsed = Literal["literature_evidence"]


class KnowledgeChatResponse(BaseModel):
    request_id: str
    conversation_id: str
    conversation_token: str | None = None
    configuration_version: str
    assistant_policy_version: str
    status: Literal[
        "answered",
        "no_evidence",
        "evidence_unavailable",
        "safety_refusal",
        "capacity_limited",
    ]
    answer: str
    evidence_state: EvidenceState
    source_used: SourceUsed
    model_id: str | None = None
    claims: list[KnowledgeClaim] = Field(default_factory=list)
    citations: list[KnowledgeCitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def answered_has_grounding(self) -> "KnowledgeChatResponse":
        expected_non_answer = {
            "no_evidence": "no_relevant_corpus_evidence",
            "evidence_unavailable": "evidence_unavailable",
            "safety_refusal": "not_applicable",
            "capacity_limited": "not_applicable",
        }
        if self.status == "answered":
            if (
                not self.claims
                or not self.citations
                or self.evidence_state
                in {
                    "no_relevant_corpus_evidence",
                    "evidence_unavailable",
                    "not_applicable",
                }
            ):
                raise ValueError("answered responses need claims, citations and an evidence state")
        elif self.evidence_state != expected_non_answer[self.status]:
            raise ValueError("evidence state must match response status")
        return self
