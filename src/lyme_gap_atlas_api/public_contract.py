"""HTTP-neutral public V1 DTOs and bounded query rules (API #52)."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field, model_validator


class ValueState(StrEnum):
    """Public V1 states, including the governed county status added by API #54."""

    OBSERVED = "OBSERVED"
    ZERO = "ZERO"
    MISSING = "MISSING"
    SUPPRESSED = "SUPPRESSED"
    UNAVAILABLE = "UNAVAILABLE"
    NO_COUNTY_LINKED_RECORD = "NO_COUNTY_LINKED_RECORD"


class GeographyType(StrEnum):
    county = "county"
    state = "state"


class GeographyIdentity(BaseModel):
    geography_type: GeographyType
    geography_id: str

    @model_validator(mode="after")
    def validate_fips(self) -> "GeographyIdentity":
        length = 5 if self.geography_type == GeographyType.county else 2
        if (
            len(self.geography_id) != length
            or not self.geography_id.isascii()
            or not self.geography_id.isdigit()
        ):
            raise ValueError(
                f"{self.geography_type.value} geography_id must be {length}-digit FIPS"
            )
        return self


class StandardsMapping(BaseModel):
    standard: str
    version: str
    target: str
    relationship: str
    mapping_reference: str | None = None


class EvidenceReference(BaseModel):
    resource_type: str
    resource_id: str
    source_id: str | None = None
    provenance_ref: str | None = None
    semantic_version: str | None = None
    methodology_version: str | None = None
    release_id: str | None = None
    uri: str | None = None


class Indicator(BaseModel):
    indicator_id: str
    label: str
    definition: str | None
    measure_ids: list[str]
    semantic_version: str
    release_version: str | None = None
    domain: str | None = None
    category: str | None = None
    limitations: list[str] = Field(default_factory=list)
    standards_mappings: list[StandardsMapping] | None = None


class Measure(BaseModel):
    measure_id: str
    indicator_id: str
    label: str
    definition: str | None
    semantic_version: str
    release_version: str | None = None
    measure_type: str | None = None
    unit: str | None = None
    denominator: str | None = None
    geography_types: list[GeographyType] | None = None
    temporal_grains: list[str] | None = None
    geography_semantics: str | None = None
    temporal_semantics: str | None = None
    allowed_value_states: list[ValueState] | None = None
    allowed_strata: list[str] | None = None
    missingness_semantics: str | None = None
    methodology_id: str | None = None
    methodology: str | None = None
    source_ids: list[str] | None = None
    limitations: list[str] = Field(default_factory=list)
    standards_mappings: list[StandardsMapping] | None = None


class Observation(BaseModel):
    observation_id: str
    measure_id: str
    geography: GeographyIdentity
    period_start: date
    period_end: date
    temporal_grain: str
    value: float | str | None
    value_state: ValueState
    unit: str
    denominator: str | None
    strata: dict[str, str] = Field(default_factory=dict)
    source_id: str
    lineage_source_id: str | None = None
    dataset_id: str | None = None
    methodology_id: str | None
    methodology: str | None = None
    release_methodology_version: str | None = None
    methodology_version: str
    semantic_version: str
    release_id: str
    provenance_ref: str
    source_label: str | None = None
    source_vintage: str | None = None
    source_url: str | None = None
    source_published_at: datetime | None = None
    atlas_acquired_at: datetime | None = None
    atlas_processed_at: datetime | None = None
    limitations: list[str]
    evidence: EvidenceReference

    @model_validator(mode="after")
    def validate_value_state(self) -> "Observation":
        if self.period_end < self.period_start:
            raise ValueError("period_end precedes period_start")
        if self.value_state == ValueState.ZERO and (
            isinstance(self.value, bool) or self.value != 0
        ):
            raise ValueError("ZERO requires numeric zero")
        if self.value_state == ValueState.OBSERVED and self.value in (None, 0):
            raise ValueError("OBSERVED requires a nonzero value")
        if (
            self.value_state
            in {
                ValueState.MISSING,
                ValueState.SUPPRESSED,
                ValueState.UNAVAILABLE,
            }
            and self.value is not None
        ):
            raise ValueError("this value state requires a null value")
        if (
            self.value_state == ValueState.NO_COUNTY_LINKED_RECORD
            and self.value != "no_county_linked_record"
        ):
            raise ValueError("NO_COUNTY_LINKED_RECORD requires the governed status value")
        for field in ("source_published_at", "atlas_acquired_at", "atlas_processed_at"):
            stamp = getattr(self, field)
            if stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None):
                raise ValueError(f"{field} must have a UTC offset")
        return self


class Geography(BaseModel):
    geography: GeographyIdentity
    label: str
    parent: GeographyIdentity | None = None
    # Geometry is a separately governed display resource and is never implied here.


class Source(BaseModel):
    source_id: str
    label: str
    publisher: str | None
    lineage_source_id: str
    dataset_id: str
    semantic_version: str
    release_version: str
    source_url: str | None = None
    source_vintage: str | None = None
    source_version: str | None = None
    published_at: datetime | None = Field(default=None, deprecated=True)
    atlas_acquired_at: datetime | None = Field(default=None, deprecated=True)
    upstream_updated_at: datetime | None = None
    source_retrieved_at: datetime | None = None
    limitations: list[str] = Field(default_factory=list)


class Methodology(BaseModel):
    methodology_id: str
    measure_id: str
    version: str
    description: str
    limitations: list[str]
    semantic_version: str
    release_version: str


class CollectionMeta(BaseModel):
    next_page_token: str | None = None
    response_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CollectionLinks(BaseModel):
    self: str | None = None


T = TypeVar("T")


class CollectionEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: CollectionMeta
    links: CollectionLinks


class ResourceEnvelope(BaseModel, Generic[T]):
    data: T


class PublicQueryError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class ObservationQuery(BaseModel):
    measure_id: str = Field(min_length=1)
    geography_type: GeographyType
    geography_id: list[str] = Field(min_length=1, max_length=500)
    year: int | None = Field(default=None, ge=1900, le=2100)
    start_date: date | None = None
    end_date: date | None = None
    stratification: list[str] = Field(default_factory=list, max_length=20)
    page_size: int = Field(default=100, ge=1, le=500)
    page_token: str | None = None

    def validate_bounds(self, *, ceiling: int, annual: bool = False) -> None:
        if self.year is not None and (self.start_date is not None or self.end_date is not None):
            raise PublicQueryError("INVALID_REQUEST", "Use year or a date range, not both.")
        if self.year is None and (self.start_date is None or self.end_date is None):
            raise PublicQueryError(
                "INVALID_REQUEST", "Supply a year or both start_date and end_date."
            )
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise PublicQueryError("INVALID_REQUEST", "end_date must not precede start_date.")
        for geography_id in self.geography_id:
            try:
                GeographyIdentity(geography_type=self.geography_type, geography_id=geography_id)
            except ValueError as exc:
                raise PublicQueryError("INVALID_REQUEST", str(exc)) from exc
        if len(set(self.geography_id)) != len(self.geography_id):
            raise PublicQueryError("INVALID_REQUEST", "Duplicate geography_id is invalid.")
        # The current projection has at most one row per county and annual bucket.
        # Keep the day-level fallback for callers without a governed annual grain.
        if self.year is not None:
            buckets = 1
        elif annual:
            buckets = self.end_date.year - self.start_date.year + 1  # type: ignore[union-attr]
        else:
            buckets = (self.end_date - self.start_date).days + 1  # type: ignore[operator]
        if len(self.geography_id) * buckets > ceiling:
            raise PublicQueryError(
                "QUERY_TOO_BROAD",
                "Narrow measure, geography, time, or stratification selection.",
            )
