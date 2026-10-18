"""HTTP-neutral public V1 DTOs and bounded query rules (API #52)."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValueState(StrEnum):
    """Public V1 states, including the governed county status added by API #54."""

    OBSERVED = "OBSERVED"
    ZERO = "ZERO"
    MISSING = "MISSING"
    SUPPRESSED = "SUPPRESSED"
    UNAVAILABLE = "UNAVAILABLE"
    NO_COUNTY_LINKED_RECORD = "NO_COUNTY_LINKED_RECORD"
    NO_RECORDS = "NO_RECORDS"


class GeographyType(StrEnum):
    county = "county"
    state = "state"


class GeographyIdentity(BaseModel):
    """Stable FIPS join identity, independent of display or analysis polygons."""

    geography_type: GeographyType
    geography_id: str = Field(
        description="FIPS string with leading zeros: five digits for counties, two for states."
    )

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
    source_id: str | None = Field(
        default=None,
        description="Public source resource key; distinct from lineage_source_id and dataset_id.",
    )
    provenance_ref: str | None = Field(
        default=None,
        description="Structured observation provenance identity; not a literature citation.",
    )
    semantic_version: str | None = Field(
        default=None,
        description=(
            "Governed semantic contract version, separate from HTTP V1 and application version."
        ),
    )
    methodology_version: str | None = Field(
        default=None,
        description="Transformation version, distinct from methodology resource version.",
    )
    release_id: str | None = Field(
        default=None, description="Governed release identity for reproducibility."
    )
    uri: str | None = None


class Indicator(BaseModel):
    indicator_id: str
    label: str
    definition: str | None
    measure_ids: list[str]
    semantic_version: str = Field(
        description=(
            "Governed semantic contract version, separate from HTTP V1 and application version."
        )
    )
    release_version: str | None = Field(
        default=None, description="Governed current-release relationship version."
    )
    domain: str | None = None
    category: str | None = None
    limitations: list[str] = Field(
        default_factory=list,
        description="Governed interpretation limitations; preserve alongside values and versions.",
    )
    standards_mappings: list[StandardsMapping] | None = None


class Measure(BaseModel):
    measure_id: str
    indicator_id: str
    label: str
    definition: str | None
    semantic_version: str = Field(
        description=(
            "Governed semantic contract version, separate from HTTP V1 and application version."
        )
    )
    release_version: str | None = Field(
        default=None, description="Governed current-release relationship version."
    )
    measure_type: str | None = None
    unit: str | None = None
    denominator: str | None = Field(
        default=None,
        description="Governed denominator; null means it is not supplied and must not be inferred.",
    )
    geography_types: list[GeographyType] | None = None
    temporal_grains: list[str] | None = None
    geography_semantics: str | None = None
    temporal_semantics: str | None = None
    allowed_value_states: list[ValueState] | None = None
    allowed_strata: list[str] | None = None
    missingness_semantics: str | None = None
    methodology_id: str | None = Field(
        default=None, description="Exact governed methodology resource ID; null when not governed."
    )
    methodology: str | None = None
    source_ids: list[str] | None = None
    limitations: list[str] = Field(
        default_factory=list,
        description="Governed interpretation limitations; preserve alongside values and versions.",
    )
    standards_mappings: list[StandardsMapping] | None = None


class EnvironmentalContext(BaseModel):
    """Coverage independent of value state; numeric strings preserve exact DECIMAL."""

    coverage_status: Literal[
        "COMPLETE", "PARTIAL_COVERAGE", "SOURCE_MISSING", "OUT_OF_SOURCE_COVERAGE"
    ]
    source_time_present: bool
    day_convention: str = Field(
        min_length=1, description="Publisher labeled 24-hour period, not a midnight calendar day."
    )
    expected_area_m2: float | str | None
    intersected_area_m2: float | str | None
    source_supported_area_m2: float | str | None
    valid_area_m2: float | str | None
    source_coverage_fraction: float | str | None = Field(
        description="Monthly source-supported/legal county area; separate from daily completeness."
    )
    valid_fraction_of_supported_area: float | str | None = Field(
        description="Daily valid/source-supported area; completeness threshold 0.95."
    )
    upstream_date_modified: str | None = Field(
        description="Upstream modification metadata; never original publication or first "
        "availability."
    )
    weight_version: str
    geometry_version: str = Field(description="Analysis geometry identity only; no polygons.")
    metadata_revision_id: str


class SurveillanceEvidenceContext(BaseModel):
    """Data-projector evidence, never an API classification."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["atlas-surveillance-evidence-v3"]
    normalized_state: Literal["established", "detected", "unknown"] | None = Field(
        default=None,
        description="Data-owned county evidence state, projected without classification. "
        "Null means the older companion did not supply normalization; unknown supports no "
        "biological absence conclusion. Original publisher value remains Observation.value.",
    )
    state: Literal[
        "established",
        "detected_below_establishment",
        "sampled_not_detected",
        "no_qualifying_record",
        "unknown",
    ]
    reason_codes: list[str] = Field(min_length=1)
    evidence_tier: Literal["GOVERNED_MAPPING"]
    observation_key: str
    revision_id: str
    value_state: str
    source_rule_id: str
    limitations: list[str] = Field(min_length=1)
    evidence_revision_id: str


class MethodologyComparisonContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["atlas-lyme-surveillance-methodology-v1"]
    comparison_state: Literal["COMPARABLE", "CAUTION_REQUIRED", "NOT_COMPARABLE", "UNKNOWN"]
    reason_codes: list[str]
    methodology_era_ids: list[str | None]
    jurisdiction_class: str | None
    references: list[str]
    limitations: list[str]
    left_observation_id: str = Field(description="Public ID of the comparison's left input.")


class TemporalWindowContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end_exclusive: date
    calendar: Literal["GREGORIAN"]
    timezone: Literal["UTC"]


class TemporalLagContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ZERO", "FIXED", "ROLLING", "SEASONAL"]
    anchor: Literal["START", "END_EXCLUSIVE"]
    offset_days: int
    window_days: int | None = None
    season_id: str | None = None


class TemporalCoverageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected: int | float
    observed: int | float
    unit: str
    partial_allowed: bool


class TemporalReferencePeriodContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    start: date
    end_exclusive: date
    source_vintage: str


class TemporalAlignmentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["atlas-temporal-alignment-v1"]
    method_id: str
    method_version: str
    source_id: str
    measure_id: str
    aggregation_rule: str
    coverage_denominator: str
    decision_cutoff: datetime
    disposition: Literal["ELIGIBLE", "INELIGIBLE", "RETROSPECTIVE_ONLY", "UNKNOWN_AVAILABILITY"]
    reason_codes: list[str]
    limitations: list[str]
    observation_window: TemporalWindowContext
    target_window: TemporalWindowContext
    lag: TemporalLagContext
    coverage: TemporalCoverageContext
    reference_period: TemporalReferencePeriodContext | None = None


class SurveillanceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: SurveillanceEvidenceContext | None = None
    methodology_comparison: MethodologyComparisonContext | None = None
    temporal_alignment: TemporalAlignmentContext | None = None


class ObservationFields(BaseModel):
    metadata_revision_id: str | None = Field(
        default=None, description="Reviewed consumer-safe metadata revision; not private lineage."
    )
    observation_id: str = Field(
        description="Stable observation identity and compact provenance reference."
    )
    measure_id: str
    geography: GeographyIdentity
    temporal_grain: str
    value: float | str | None = Field(
        description="Read with value_state and unit; null never means zero."
    )
    value_state: ValueState = Field(
        description=(
            "OBSERVED is nonzero; ZERO is numeric zero; MISSING/SUPPRESSED/UNAVAILABLE "
            "require null; NO_COUNTY_LINKED_RECORD retains the literal "
            "no_county_linked_record; NO_RECORDS retains CDC's No records. "
            "Neither establishes biological absence."
        )
    )
    unit: str
    denominator: str | None = Field(
        description="Governed denominator; null means it is not supplied and must not be inferred."
    )
    strata: dict[str, str] = Field(default_factory=dict)
    source_id: str = Field(
        description="Public source resource key; distinct from lineage_source_id and dataset_id."
    )
    lineage_source_id: str | None = None
    dataset_id: str | None = None
    methodology_id: str | None = Field(
        description="Exact governed methodology resource ID; null when not governed."
    )
    methodology: str | None = None
    release_methodology_version: str | None = None
    methodology_version: str = Field(
        description="Transformation version, distinct from methodology resource version."
    )
    semantic_version: str = Field(
        description=(
            "Governed semantic contract version, separate from HTTP V1 and application version."
        )
    )
    release_id: str = Field(description="Governed release identity for reproducibility.")
    provenance_ref: str = Field(
        description="Structured observation provenance identity; not a literature citation."
    )
    source_label: str | None = None
    source_vintage: str | None = None
    source_url: str | None = None
    source_published_at: datetime | None = Field(
        default=None,
        description="Publisher timestamp when governed; null is not inferred freshness.",
    )
    atlas_acquired_at: datetime | None = Field(
        default=None,
        description=(
            "Atlas acquisition time when governed; distinct from source publication and processing."
        ),
    )
    atlas_processed_at: datetime | None = Field(
        default=None,
        description="Atlas processing time when governed; distinct from response time.",
    )
    limitations: list[str] = Field(
        description="Governed interpretation limitations; preserve alongside values and versions."
    )
    evidence: EvidenceReference
    environmental_context: EnvironmentalContext | None = Field(
        default=None,
        description="Present only for reviewed environmental context. DECIMAL quantities may use "
        "exact numeric strings; native DOUBLE remains a JSON number.",
    )
    surveillance_context: SurveillanceContext | None = Field(
        default=None,
        description="Only supplied through an admitted Data release/bundle and supported slice. "
        "Omitted or null carries no evidence-state, comparability or lag claim. "
        "Comparison is case-definition scope only; alignment is descriptive, not causal.",
    )

    @model_validator(mode="after")
    def validate_value_state(self) -> "ObservationFields":
        if self.value_state == ValueState.NO_RECORDS and self.value != "No records":
            raise ValueError("NO_RECORDS requires the original CDC publisher value")
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
    """County/state identity metadata; geometry is a separate display resource."""

    geography: GeographyIdentity
    label: str
    parent: GeographyIdentity | None = None
    # Geometry is a separately governed display resource and is never implied here.


class Observation(ObservationFields):
    period_start: date = Field(description="Inclusive start of the governed observation period.")
    period_end: date = Field(description="Inclusive end of the governed observation period.")

    @model_validator(mode="after")
    def validate_period(self) -> "Observation":
        if self.period_end < self.period_start:
            raise ValueError("period_end precedes period_start")
        return self


class CountyContext(BaseModel):
    contract_version: Literal["atlas-county-context-consumer/1"]
    vintage_year: int = Field(
        description="Source product/classification vintage, not an annual date."
    )
    source_field: str
    value_type: Literal["integer", "number"]
    category_label: str | None = Field(
        description="Authoritative category label; null for quantities."
    )
    definition: str
    allowed_value_states: list[ValueState]
    metadata_methodology_version: str


class CountyContextObservation(ObservationFields):
    value: int | str | None = Field(
        description="Integer population/category or exact nonzero decimal text; ZERO is integer 0."
    )
    period_start: date | None = Field(
        description="ACS reference-period start; null for RUCC vintage."
    )
    period_end: date | None = Field(description="ACS reference-period end; null for RUCC vintage.")
    temporal_grain: Literal["PERIOD", "VINTAGE_YEAR"]
    county_context: CountyContext

    @model_validator(mode="after")
    def validate_context_time(self) -> "CountyContextObservation":
        if self.temporal_grain == "VINTAGE_YEAR":
            if self.period_start is not None or self.period_end is not None:
                raise ValueError("Vintage classifications have no observation dates")
        elif (
            self.period_start is None
            or self.period_end is None
            or self.period_end < self.period_start
        ):
            raise ValueError("Reference period requires ordered dates")
        return self


PublicObservation = CountyContextObservation | Observation


class Source(BaseModel):
    source_id: str = Field(
        description="Public source resource key; distinct from lineage_source_id and dataset_id."
    )
    label: str
    publisher: str | None
    lineage_source_id: str
    dataset_id: str
    semantic_version: str = Field(
        description=(
            "Governed semantic contract version, separate from HTTP V1 and application version."
        )
    )
    release_version: str = Field(description="Governed current-release relationship version.")
    source_url: str | None = None
    source_vintage: str | None = None
    source_version: str | None = None
    published_at: datetime | None = Field(default=None, deprecated=True)
    atlas_acquired_at: datetime | None = Field(
        default=None,
        deprecated=True,
        description=(
            "Atlas acquisition time when governed; distinct from source publication and processing."
        ),
    )
    upstream_updated_at: datetime | None = Field(
        default=None, description="Governed publisher update time; null when unavailable."
    )
    source_retrieved_at: datetime | None = Field(
        default=None, description="Governed source retrieval time; null when unavailable."
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Governed interpretation limitations; preserve alongside values and versions.",
    )


class Methodology(BaseModel):
    methodology_id: str = Field(
        description="Exact governed methodology resource ID; null when not governed."
    )
    measure_id: str
    version: str
    description: str
    limitations: list[str] = Field(
        description="Governed interpretation limitations; preserve alongside values and versions."
    )
    semantic_version: str = Field(
        description=(
            "Governed semantic contract version, separate from HTTP V1 and application version."
        )
    )
    release_version: str = Field(description="Governed current-release relationship version.")


class CollectionMeta(BaseModel):
    next_page_token: str | None = Field(
        default=None,
        description=(
            "Opaque query/release-bound continuation. Null means exhaustion; tokens may "
            "expire on restart."
        ),
    )
    response_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC response construction time; not source freshness or observation time.",
    )
    availability: Literal["AVAILABLE", "UNAVAILABLE"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Observation selection availability, not biological presence. Omitted on other "
            "collections. An exhausted continuation remains AVAILABLE."
        ),
    )
    reason: Literal["NO_PUBLISHED_RESULTS"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "No published rows after a successful initial non-CDC query; otherwise omitted."
        ),
    )
    release_id: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Current governed release actually read for this query.",
    )
    limitations: list[str] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Selection availability caveats; observations only.",
    )

    @model_validator(mode="after")
    def validate_availability(self) -> "CollectionMeta":
        if self.availability == "AVAILABLE":
            if self.reason is not None:
                raise ValueError("AVAILABLE requires a null reason")
        elif self.availability == "UNAVAILABLE" and (
            self.reason != "NO_PUBLISHED_RESULTS"
            or not self.release_id
            or not self.release_id.strip()
            or self.next_page_token is not None
            or not self.limitations
        ):
            raise ValueError(
                "UNAVAILABLE requires a release, reason, limitations and no continuation"
            )
        return self


class CollectionLinks(BaseModel):
    self: str | None = None


T = TypeVar("T")


class CollectionEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: CollectionMeta
    links: CollectionLinks

    @model_validator(mode="after")
    def validate_unavailable_data(self) -> "CollectionEnvelope[T]":
        if self.meta.availability == "UNAVAILABLE" and self.data:
            raise ValueError("UNAVAILABLE must not fabricate observation records")
        return self


class ResourceEnvelope(BaseModel, Generic[T]):
    data: T


class PublicQueryError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


# The branches intentionally use anyOf: period-based county-context records can
# also satisfy the standard observation shape because Pydantic models permit
# extra fields. Vintage county-context rows are disjoint, but that does not make
# the union mutually exclusive for all valid data.
ObservationCollection = (
    CollectionEnvelope[CountyContextObservation] | CollectionEnvelope[Observation]
)


class ObservationQuery(BaseModel):
    measure_id: str = Field(min_length=1)
    geography_type: GeographyType
    geography_id: list[str] = Field(
        min_length=1,
        max_length=500,
        description="FIPS string with leading zeros: five digits for counties, two for states.",
    )
    vintage_year: int | None = Field(default=None, ge=1900, le=2100)
    year: int | None = Field(default=None, ge=1900, le=2100)
    start_date: date | None = None
    end_date: date | None = None
    stratification: list[str] = Field(default_factory=list, max_length=20)
    page_size: int = Field(default=100, ge=1, le=500)
    page_token: str | None = None

    def validate_bounds(self, *, ceiling: int, annual: bool = False) -> None:
        for geography_id in self.geography_id:
            try:
                GeographyIdentity(geography_type=self.geography_type, geography_id=geography_id)
            except ValueError as exc:
                raise PublicQueryError("INVALID_REQUEST", str(exc)) from exc
        if len(set(self.geography_id)) != len(self.geography_id):
            raise PublicQueryError("INVALID_REQUEST", "Duplicate geography_id is invalid.")
        from .county_context_ids import CONTEXT_FIELDS

        if self.measure_id in CONTEXT_FIELDS:
            expected = 2023 if self.measure_id == "rucc_2023" else 2022
            if self.vintage_year is None:
                raise PublicQueryError("INVALID_REQUEST", "Supply vintage_year for county context.")
            if (
                self.vintage_year != expected
                or self.year is not None
                or self.start_date is not None
                or self.end_date is not None
            ):
                raise PublicQueryError(
                    "UNSUPPORTED_FILTER", "Unsupported county context time selection."
                )
            if len(self.geography_id) > ceiling:
                raise PublicQueryError("QUERY_TOO_BROAD", "Narrow the county selection.")
            return
        if self.vintage_year is not None:
            raise PublicQueryError(
                "UNSUPPORTED_FILTER", "This measure does not support vintage_year."
            )
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
        # The current projection has at most one row per county and annual bucket.
        # Keep the day-level fallback for callers without a governed annual grain.
        if self.year is not None:
            buckets = 1 if annual else (date(self.year, 12, 31) - date(self.year, 1, 1)).days + 1
        elif annual:
            buckets = self.end_date.year - self.start_date.year + 1  # type: ignore[union-attr]
        else:
            buckets = (self.end_date - self.start_date).days + 1  # type: ignore[operator]
        if len(self.geography_id) * buckets > ceiling:
            raise PublicQueryError(
                "QUERY_TOO_BROAD",
                "Narrow measure, geography, time, or stratification selection.",
            )
