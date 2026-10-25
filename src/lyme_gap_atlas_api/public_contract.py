"""HTTP-neutral public V1 DTOs and bounded query rules."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

# One source for text that several schemas repeat.
SEMANTIC_VERSION_DESCRIPTION = (
    "Version of the data contract the value follows. It is separate from the API version "
    "and the application version."
)
RELEASE_VERSION_DESCRIPTION = "Identifies the published data release this record belongs to."
LIMITATIONS_DESCRIPTION = (
    "Factual notes on what the result does not show or where its evidence is bounded. "
    "Keep them with the value when you display or store it."
)
SOURCE_ID_DESCRIPTION = (
    "Public key of the source resource. Use it with `/v1/sources/{source_id}`. "
    "It differs from `lineage_source_id` and `dataset_id`."
)
METHODOLOGY_ID_DESCRIPTION = (
    "Identifies the methodology resource. Use it with `/v1/methodologies/{methodology_id}`. "
    "`null` when no methodology resource applies."
)
METHODOLOGY_VERSION_DESCRIPTION = (
    "Version of the transformation applied to this observation. "
    "It is separate from the version of the methodology resource."
)
DENOMINATOR_DESCRIPTION = (
    "What the value is measured against, such as a population. "
    "`null` means Atlas supplies no denominator; do not infer one."
)
FIPS_DESCRIPTION = (
    "FIPS code with leading zeros: five digits for a county, two for a state. Example: `08001`."
)
RELEASE_ID_DESCRIPTION = (
    "Identifies the published data release the result came from. "
    "Results from different releases are not directly comparable."
)
CONTRACT_VERSION_DESCRIPTION = "Version of the contract that defines this object."

VALUE_STATE_DESCRIPTION = (
    "How to read an observation's `value`. Check it before you use the value.\n\n"
    "- `OBSERVED`: a nonzero value the publisher reported. Use `value` as given.\n"
    "- `ZERO`: a reported numeric zero. It is different from a missing value.\n"
    "- `MISSING`: no value was reported and `value` is `null`. It is not zero.\n"
    "- `SUPPRESSED`: the value exists but is withheld, and `value` is `null`. "
    "It is not zero. Do not estimate it.\n"
    "- `UNAVAILABLE`: Atlas has no published result for this selection, and `value` is "
    "`null`. It does not mean zero or absence.\n"
    "- `NO_COUNTY_LINKED_RECORD`: a governed status meaning no record is linked to the "
    "county. `value` is the text `no_county_linked_record`. It is neither null nor zero.\n"
    '- `NO_RECORDS`: the publisher\'s literal "No records" result. `value` is the text '
    "`No records`. It does not establish biological absence.\n\n"
    "Show these states differently from each other. Never turn `null` into zero."
)


class ValueState(StrEnum):
    OBSERVED = "OBSERVED"
    ZERO = "ZERO"
    MISSING = "MISSING"
    SUPPRESSED = "SUPPRESSED"
    UNAVAILABLE = "UNAVAILABLE"
    NO_COUNTY_LINKED_RECORD = "NO_COUNTY_LINKED_RECORD"
    NO_RECORDS = "NO_RECORDS"


ValueState.__doc__ = VALUE_STATE_DESCRIPTION


class GeographyType(StrEnum):
    """Kind of place. `county` uses five-digit FIPS and `state` uses two-digit FIPS."""

    county = "county"
    state = "state"


class GeographyIdentity(BaseModel):
    """Identifies one place by type and FIPS code. It carries no geometry."""

    geography_type: GeographyType = Field(
        description="Kind of place: `county` or `state`. It sets how long `geography_id` is."
    )
    geography_id: str = Field(description=FIPS_DESCRIPTION)

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
    """Optional reviewed link from a public-health concept to an external standard."""

    standard: str = Field(description="Name of the external standard, such as `LOINC`.")
    version: str = Field(description="Version of the external standard that the mapping targets.")
    target: str = Field(description="Code or element in the external standard.")
    relationship: str = Field(
        description="How the Atlas concept relates to the target, such as `exact` or `broader`."
    )
    mapping_reference: str | None = Field(
        default=None, description="Reference to the reviewed mapping record. `null` when none."
    )


class EvidenceReference(BaseModel):
    """Compact pointer to the Atlas resource that backs a result."""

    resource_type: str = Field(
        description="Kind of Atlas resource the reference points to, such as `observation`."
    )
    resource_id: str = Field(description="Identifier of that resource.")
    source_id: str | None = Field(default=None, description=SOURCE_ID_DESCRIPTION)
    provenance_ref: str | None = Field(
        default=None,
        description="Identifies the provenance record for the observation. It is not a "
        "literature citation.",
    )
    semantic_version: str | None = Field(default=None, description=SEMANTIC_VERSION_DESCRIPTION)
    methodology_version: str | None = Field(
        default=None, description=METHODOLOGY_VERSION_DESCRIPTION
    )
    release_id: str | None = Field(default=None, description=RELEASE_ID_DESCRIPTION)
    uri: str | None = Field(
        default=None, description="Web address of the referenced resource, when one exists."
    )


class Indicator(BaseModel):
    """A public-health concept that groups related measures."""

    indicator_id: str = Field(
        description="Stable identifier of the indicator. "
        "Use it with `/v1/indicators/{indicator_id}`."
    )
    label: str = Field(description="Short display name of the indicator.")
    definition: str | None = Field(
        description="Plain-language definition of the indicator. `null` when none is published."
    )
    measure_ids: list[str] = Field(
        description="Identifiers of the measures that belong to this indicator, in sorted order."
    )
    semantic_version: str = Field(description=SEMANTIC_VERSION_DESCRIPTION)
    release_version: str | None = Field(default=None, description=RELEASE_VERSION_DESCRIPTION)
    domain: str | None = Field(
        default=None,
        description="Broad subject area the indicator belongs to, such as `climate`. "
        "`null` when none is published.",
    )
    category: str | None = Field(
        default=None,
        description="Finer grouping inside the domain. `null` when none is published.",
    )
    limitations: list[str] = Field(default_factory=list, description=LIMITATIONS_DESCRIPTION)
    standards_mappings: list[StandardsMapping] | None = Field(
        default=None,
        description="Optional reviewed links to external standards. `null` when none exist. "
        "A mapping does not mean Atlas exchanges data with that standard.",
    )


class Measure(BaseModel):
    """One variable you can query, such as a case count. It belongs to one indicator."""

    measure_id: str = Field(
        description="Stable identifier of the measure. Pass it as `measure_id` to "
        "`/v1/observations`.",
        examples=["case_count_floor_2023"],
    )
    indicator_id: str = Field(description="Identifier of the indicator this measure belongs to.")
    label: str = Field(description="Short display name of the measure.")
    definition: str | None = Field(
        description="Plain-language definition of the measure. `null` when none is published."
    )
    semantic_version: str = Field(description=SEMANTIC_VERSION_DESCRIPTION)
    release_version: str | None = Field(default=None, description=RELEASE_VERSION_DESCRIPTION)
    measure_type: str | None = Field(
        default=None,
        description="How the value is produced, such as `DERIVED`. `null` when none is published.",
    )
    unit: str | None = Field(
        default=None,
        description="Unit of the value, such as `mm` or `degree_Celsius`. "
        "`null` when the measure has no unit.",
    )
    denominator: str | None = Field(default=None, description=DENOMINATOR_DESCRIPTION)
    geography_types: list[GeographyType] | None = Field(
        default=None,
        description="Kinds of place the measure covers. `null` when not listed. "
        "Listing a measure does not promise data for every county and period.",
    )
    temporal_grains: list[str] | None = Field(
        default=None,
        description="Time intervals the measure is reported in, such as `DAY`. "
        "`null` when not listed.",
    )
    geography_semantics: str | None = Field(
        default=None,
        description="What the place dimension means for this measure. `null` when not published.",
    )
    temporal_semantics: str | None = Field(
        default=None,
        description="What the time dimension means for this measure. `null` when not published.",
    )
    allowed_value_states: list[ValueState] | None = Field(
        default=None,
        description="The `value_state` values this measure can return. `null` when not listed.",
    )
    allowed_strata: list[str] | None = Field(
        default=None,
        description="Breakdowns, such as age group, that the measure supports. "
        "`null` when it has none.",
    )
    missingness_semantics: str | None = Field(
        default=None,
        description="How the measure reports missing, withheld, or unavailable values. "
        "Read it before you treat `null` as a finding.",
    )
    methodology_id: str | None = Field(default=None, description=METHODOLOGY_ID_DESCRIPTION)
    methodology: str | None = Field(
        default=None, description="Short name of the method that produces the value."
    )
    source_ids: list[str] | None = Field(
        default=None,
        description="Identifiers of the sources the measure draws on. `null` when not listed.",
    )
    limitations: list[str] = Field(default_factory=list, description=LIMITATIONS_DESCRIPTION)
    standards_mappings: list[StandardsMapping] | None = Field(
        default=None,
        description="Optional reviewed links to external standards. `null` when none exist.",
    )


class EnvironmentalContext(BaseModel):
    """How completely the source covers a county and day. It is separate from `value_state`.

    Area values are square meters. Exact decimal quantities may be strings.
    """

    coverage_status: Literal[
        "COMPLETE", "PARTIAL_COVERAGE", "SOURCE_MISSING", "OUT_OF_SOURCE_COVERAGE"
    ] = Field(description="How much of the county the source covers for this day.")
    source_time_present: bool = Field(
        description="Whether the source supplied a time for this observation."
    )
    day_convention: str = Field(
        min_length=1,
        description="How the publisher defines a day. It is a labeled 24-hour period, "
        "not necessarily a midnight-to-midnight calendar day.",
    )
    expected_area_m2: float | str | None = Field(
        description="County area expected to be covered, in square meters. `null` when unknown."
    )
    intersected_area_m2: float | str | None = Field(
        description="County area that overlaps the source grid, in square meters. "
        "`null` when unknown."
    )
    source_supported_area_m2: float | str | None = Field(
        description="County area the source supports, in square meters. `null` when unknown."
    )
    valid_area_m2: float | str | None = Field(
        description="County area with a valid source value for this day, in square meters. "
        "`null` when unknown."
    )
    source_coverage_fraction: float | str | None = Field(
        description="Monthly share of the county area that the source supports. "
        "It is separate from daily completeness."
    )
    valid_fraction_of_supported_area: float | str | None = Field(
        description="Daily share of the supported area that has a valid value. "
        "Atlas treats 0.95 as the completeness threshold."
    )
    upstream_date_modified: str | None = Field(
        description="Modification date the source reports. It is not the original publication "
        "date or the date the data first became available."
    )
    weight_version: str = Field(
        description="Version of the area weights used to combine grid cells into a county value."
    )
    geometry_version: str = Field(
        description="Version of the county boundaries used for the calculation. "
        "The API returns no boundary geometry."
    )
    metadata_revision_id: str = Field(
        description="Identifies the reviewed metadata revision that describes this context."
    )


class SurveillanceEvidenceContext(BaseModel):
    """Evidence state for a county as supplied with the data. Atlas adds no classification."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["atlas-surveillance-evidence-v3"] = Field(
        description=CONTRACT_VERSION_DESCRIPTION
    )
    normalized_state: Literal["established", "detected", "unknown"] | None = Field(
        default=None,
        description="County evidence state supplied with the data, without extra "
        "classification. `unknown` supports no conclusion about biological absence. "
        "`null` means no normalized state was supplied. The publisher's original value "
        "stays in the observation `value`.",
    )
    state: Literal[
        "established",
        "detected_below_establishment",
        "sampled_not_detected",
        "no_qualifying_record",
        "unknown",
    ] = Field(description="Evidence state for the county. None of these values proves absence.")
    reason_codes: list[str] = Field(
        min_length=1, description="Codes that explain why the state applies."
    )
    evidence_tier: Literal["GOVERNED_MAPPING"] = Field(
        description="How the state was derived. `GOVERNED_MAPPING` means a reviewed mapping."
    )
    observation_key: str = Field(description="Identifies the observation this state describes.")
    revision_id: str = Field(description="Identifies the revision of the evidence record.")
    value_state: str = Field(
        description="The `value_state` of the observation this state describes."
    )
    source_rule_id: str = Field(description="Identifies the rule that mapped the source value.")
    limitations: list[str] = Field(min_length=1, description=LIMITATIONS_DESCRIPTION)
    evidence_revision_id: str = Field(description="Identifies the revision of the evidence set.")


class MethodologyComparisonContext(BaseModel):
    """Whether this observation can be compared with another across surveillance methods."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["atlas-lyme-surveillance-methodology-v1"] = Field(
        description=CONTRACT_VERSION_DESCRIPTION
    )
    comparison_state: Literal["COMPARABLE", "CAUTION_REQUIRED", "NOT_COMPARABLE", "UNKNOWN"] = (
        Field(
            description="Whether the case definitions allow a comparison. `UNKNOWN` is not "
            "`COMPARABLE`."
        )
    )
    reason_codes: list[str] = Field(description="Codes that explain the comparison state.")
    methodology_era_ids: list[str | None] = Field(
        description="Surveillance case-definition eras the compared values fall in. "
        "An entry is `null` when the era is unknown."
    )
    jurisdiction_class: str | None = Field(
        description="Group of jurisdictions that share a reporting approach. `null` when unknown."
    )
    references: list[str] = Field(description="Public references that support the comparison.")
    limitations: list[str] = Field(description=LIMITATIONS_DESCRIPTION)
    left_observation_id: str = Field(
        description="Identifier of the observation on the left side of the comparison."
    )


class TemporalWindowContext(BaseModel):
    """A span of time with an inclusive start and an exclusive end."""

    model_config = ConfigDict(extra="forbid")

    start: date = Field(description="First date in the window.")
    end_exclusive: date = Field(description="First date after the window. It is not in the window.")
    calendar: Literal["GREGORIAN"] = Field(description="Calendar the dates use.")
    timezone: Literal["UTC"] = Field(description="Time zone the dates use.")


class TemporalLagContext(BaseModel):
    """How far an observation window sits before its target window."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ZERO", "FIXED", "ROLLING", "SEASONAL"] = Field(
        description="Type of lag rule that applies."
    )
    anchor: Literal["START", "END_EXCLUSIVE"] = Field(
        description="Edge of the target window that the lag is measured from."
    )
    offset_days: int = Field(description="Number of days between the anchor and the window.")
    window_days: int | None = Field(
        default=None, description="Length of a rolling window in days. `null` for other kinds."
    )
    season_id: str | None = Field(
        default=None, description="Identifies the season for a seasonal lag. `null` otherwise."
    )


class TemporalCoverageContext(BaseModel):
    """How much of the expected time span the observation covers."""

    model_config = ConfigDict(extra="forbid")

    expected: int | float = Field(description="Amount of coverage expected, in `unit`.")
    observed: int | float = Field(description="Amount of coverage present, in `unit`.")
    unit: str = Field(description="Unit of `expected` and `observed`, such as `day`.")
    partial_allowed: bool = Field(
        description="Whether the method accepts less than the expected coverage."
    )


class TemporalReferencePeriodContext(BaseModel):
    """The reference period of a source dataset."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Identifies the reference period.")
    start: date = Field(description="First date in the reference period.")
    end_exclusive: date = Field(
        description="First date after the reference period. It is not in the period."
    )
    source_vintage: str = Field(
        description="Reference year or period of the source dataset. It is not an annual value."
    )


class TemporalAlignmentContext(BaseModel):
    """Describes how an observation's time span lines up with a target time span.

    The alignment is descriptive. It does not show cause.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["atlas-temporal-alignment-v1"] = Field(
        description=CONTRACT_VERSION_DESCRIPTION
    )
    method_id: str = Field(description="Identifies the alignment method.")
    method_version: str = Field(description="Version of the alignment method.")
    source_id: str = Field(description="Identifies the source of the aligned observation.")
    measure_id: str = Field(description="Identifies the measure that was aligned.")
    aggregation_rule: str = Field(
        description="Rule used to combine values inside the observation window."
    )
    coverage_denominator: str = Field(
        description="What the coverage is counted against, such as days in the window."
    )
    decision_cutoff: datetime = Field(
        description="Time after which new data is not used for the alignment. "
        "Includes a UTC offset."
    )
    disposition: Literal["ELIGIBLE", "INELIGIBLE", "RETROSPECTIVE_ONLY", "UNKNOWN_AVAILABILITY"] = (
        Field(description="Whether the observation can be used for the target window.")
    )
    reason_codes: list[str] = Field(description="Codes that explain the disposition.")
    limitations: list[str] = Field(description=LIMITATIONS_DESCRIPTION)
    observation_window: TemporalWindowContext = Field(
        description="Time span the observation covers."
    )
    target_window: TemporalWindowContext = Field(description="Time span being compared against.")
    lag: TemporalLagContext = Field(description="Lag between the two windows.")
    coverage: TemporalCoverageContext = Field(
        description="Expected and observed coverage of the observation window."
    )
    reference_period: TemporalReferencePeriodContext | None = Field(
        default=None,
        description="Reference period of the source dataset. `null` when none applies.",
    )


class SurveillanceContext(BaseModel):
    """Optional surveillance evidence attached to an observation.

    A missing or `null` part supports no claim about evidence state, comparability, or lag.
    Comparison covers case definitions only. Alignment is descriptive, not causal.
    """

    model_config = ConfigDict(extra="forbid")

    evidence: SurveillanceEvidenceContext | None = Field(
        default=None, description="County evidence state. `null` when none is supplied."
    )
    methodology_comparison: MethodologyComparisonContext | None = Field(
        default=None,
        description="Comparability of case definitions. `null` when none is supplied.",
    )
    temporal_alignment: TemporalAlignmentContext | None = Field(
        default=None, description="Time alignment details. `null` when none is supplied."
    )


class ObservationFields(BaseModel):
    """Fields shared by every public observation: a value with its state and provenance."""

    metadata_revision_id: str | None = Field(
        default=None,
        description="Identifies the reviewed metadata revision that describes this observation. "
        "`null` when none applies.",
    )
    observation_id: str = Field(
        description="Stable identifier of the observation. It also serves as the provenance "
        "reference."
    )
    measure_id: str = Field(
        description="Identifier of the measure. See `/v1/measures/{measure_id}`."
    )
    geography: GeographyIdentity = Field(description="The place the value describes.")
    temporal_grain: str = Field(
        description="Time interval the value covers, such as `YEAR` or `DAY`."
    )
    value: float | str | None = Field(
        description="The reported value. Read it with `value_state` and `unit`. "
        "`null` never means zero."
    )
    value_state: ValueState = Field(
        description="How to read `value`. The `ValueState` schema explains each state and what "
        "a client should do. Never treat a `null` value as zero."
    )
    unit: str = Field(description="Unit of `value`, such as `count` or `mm`.")
    denominator: str | None = Field(description=DENOMINATOR_DESCRIPTION)
    strata: dict[str, str] = Field(
        default_factory=dict,
        description="Breakdown the value applies to, such as an age group. Empty when the "
        "release has no strata.",
    )
    source_id: str = Field(description=SOURCE_ID_DESCRIPTION)
    lineage_source_id: str | None = Field(
        default=None,
        description="Identifies the source in Atlas lineage records. It differs from `source_id`.",
    )
    dataset_id: str | None = Field(
        default=None,
        description="Identifies the publisher's dataset. It differs from `source_id`.",
    )
    methodology_id: str | None = Field(description=METHODOLOGY_ID_DESCRIPTION)
    methodology: str | None = Field(
        default=None, description="Short name of the method that produced the value."
    )
    release_methodology_version: str | None = Field(
        default=None,
        description="Version of the methodology as published in this release. `null` when none.",
    )
    methodology_version: str = Field(description=METHODOLOGY_VERSION_DESCRIPTION)
    semantic_version: str = Field(description=SEMANTIC_VERSION_DESCRIPTION)
    release_id: str = Field(description=RELEASE_ID_DESCRIPTION)
    provenance_ref: str = Field(
        description="Identifies the provenance record for this observation. It is not a "
        "literature citation."
    )
    source_label: str | None = Field(
        default=None, description="Display name of the source. `null` when none is published."
    )
    source_vintage: str | None = Field(
        default=None,
        description="Reference year or period of the source dataset, such as `ACS 2018-2022`. "
        "It is not an annual value.",
    )
    source_url: str | None = Field(
        default=None, description="Web address of the source. `null` when none is published."
    )
    source_published_at: datetime | None = Field(
        default=None,
        description="When the publisher published the data. Includes a UTC offset. `null` when "
        "not recorded; do not assume the data is fresh.",
    )
    atlas_acquired_at: datetime | None = Field(
        default=None,
        description="When Atlas retrieved the data from the publisher. Includes a UTC offset. "
        "`null` when not recorded.",
    )
    atlas_processed_at: datetime | None = Field(
        default=None,
        description="When Atlas processed the data. Includes a UTC offset. It is not the "
        "response time. `null` when not recorded.",
    )
    limitations: list[str] = Field(description=LIMITATIONS_DESCRIPTION)
    evidence: EvidenceReference = Field(
        description="Compact pointer to the Atlas resource that backs this observation."
    )
    environmental_context: EnvironmentalContext | None = Field(
        default=None,
        description="Coverage details. Present only for environmental measures. "
        "Exact decimal quantities may be strings.",
    )
    surveillance_context: SurveillanceContext | None = Field(
        default=None,
        description="Optional surveillance evidence. Omitted or `null` supports no claim about "
        "evidence state, comparability, or lag.",
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
    """Identity and display name of a county or state. It carries no geometry."""

    geography: GeographyIdentity = Field(description="Type and FIPS code of the place.")
    label: str = Field(description="Display name of the place.")
    parent: GeographyIdentity | None = Field(
        default=None, description="The state that contains a county. `null` for a state."
    )
    # Geometry is a separately governed display resource and is never implied here.


class Observation(ObservationFields):
    """One value for a measure, a county, and a period, with its state and provenance."""

    period_start: date = Field(description="First date the observation covers (inclusive).")
    period_end: date = Field(description="Last date the observation covers (inclusive).")

    @model_validator(mode="after")
    def validate_period(self) -> "Observation":
        if self.period_end < self.period_start:
            raise ValueError("period_end precedes period_start")
        return self


class CountyContext(BaseModel):
    """Describes the county context field that a population or rurality value came from."""

    contract_version: Literal["atlas-county-context-consumer/1"] = Field(
        description=CONTRACT_VERSION_DESCRIPTION
    )
    vintage_year: int = Field(
        description="Year of the source product or classification. It is not an annual date.",
        examples=[2023],
    )
    source_field: str = Field(description="Name of the field in the source product.")
    value_type: Literal["integer", "number"] = Field(
        description="Whether `value` is an integer or a decimal number."
    )
    category_label: str | None = Field(
        description="Official label of the category. `null` for quantities."
    )
    definition: str = Field(description="Plain-language definition of the field.")
    allowed_value_states: list[ValueState] = Field(
        description="The `value_state` values this field can return."
    )
    metadata_methodology_version: str = Field(
        description="Version of the methodology metadata that describes this field."
    )


class CountyContextObservation(ObservationFields):
    """A county population or rurality value, reported for a reference period or a vintage."""

    value: int | str | None = Field(
        description="An integer for a population or category, or exact decimal text. "
        "`ZERO` is the integer 0. Read it with `value_state`."
    )
    period_start: date | None = Field(
        description="First date of the reference period. `null` for a classification vintage."
    )
    period_end: date | None = Field(
        description="Last date of the reference period. `null` for a classification vintage."
    )
    temporal_grain: Literal["PERIOD", "VINTAGE_YEAR"] = Field(
        description="`PERIOD` for a dated reference period. `VINTAGE_YEAR` for a classification "
        "that has no dates."
    )
    county_context: CountyContext = Field(description="Describes the field the value came from.")

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
    """The organization and dataset a value came from."""

    source_id: str = Field(description=SOURCE_ID_DESCRIPTION)
    label: str = Field(description="Display name of the source.")
    publisher: str | None = Field(
        description="Organization that publishes the data. `null` when none is recorded."
    )
    lineage_source_id: str = Field(
        description="Identifies the source in Atlas lineage records. It differs from `source_id`."
    )
    dataset_id: str = Field(
        description="Identifies the publisher's dataset. It differs from `source_id`."
    )
    semantic_version: str = Field(description=SEMANTIC_VERSION_DESCRIPTION)
    release_version: str = Field(description=RELEASE_VERSION_DESCRIPTION)
    source_url: str | None = Field(
        default=None, description="Web address of the source. `null` when none is published."
    )
    source_vintage: str | None = Field(
        default=None,
        description="Reference year or period of the source dataset. It is not an annual value.",
    )
    source_version: str | None = Field(
        default=None, description="Version label the publisher gives the dataset, when it has one."
    )
    published_at: datetime | None = Field(
        default=None,
        deprecated=True,
        description="Deprecated. Use `upstream_updated_at`. When the publisher published the data.",
    )
    atlas_acquired_at: datetime | None = Field(
        default=None,
        deprecated=True,
        description="Deprecated. Use `source_retrieved_at`. When Atlas retrieved the data.",
    )
    upstream_updated_at: datetime | None = Field(
        default=None,
        description="When the publisher last updated the data. `null` when unavailable. "
        "Do not use the response time in its place.",
    )
    source_retrieved_at: datetime | None = Field(
        default=None,
        description="When Atlas last retrieved the data from the publisher. `null` when "
        "unavailable.",
    )
    limitations: list[str] = Field(default_factory=list, description=LIMITATIONS_DESCRIPTION)


class Methodology(BaseModel):
    """The method Atlas used to produce or interpret a measure's values."""

    methodology_id: str = Field(
        description="Identifies the methodology. Use it with `/v1/methodologies/{methodology_id}`."
    )
    measure_id: str = Field(description="Identifier of the measure the methodology applies to.")
    version: str = Field(description="Version of this methodology resource.")
    description: str = Field(description="Plain-language description of the method.")
    limitations: list[str] = Field(description=LIMITATIONS_DESCRIPTION)
    semantic_version: str = Field(description=SEMANTIC_VERSION_DESCRIPTION)
    release_version: str = Field(description=RELEASE_VERSION_DESCRIPTION)


class CollectionMeta(BaseModel):
    """Paging, release, and availability details for a list response."""

    next_page_token: str | None = Field(
        default=None,
        description="Send this value as `page_token` to get the next page, with the same "
        "filters. Do not parse it. `null` means there are no more pages. A token can stop "
        "working after a service restart.",
    )
    response_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When Atlas built this response, in UTC. It is not when the publisher last "
        "updated the data and not when the observation was made.",
    )
    availability: Literal["AVAILABLE", "UNAVAILABLE"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Whether Atlas has published results for the selection. It does not "
        "describe biological presence. Returned for observations and omitted on other "
        "collections. A later page that runs out of items stays `AVAILABLE`.",
    )
    reason: Literal["NO_PUBLISHED_RESULTS"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Why `availability` is `UNAVAILABLE`. `NO_PUBLISHED_RESULTS` means the first "
        "query found no published results. Omitted otherwise.",
    )
    release_id: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Identifies the published data release this query read. Results from "
        "different releases are not directly comparable.",
    )
    limitations: list[str] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Notes on what the availability result does and does not show. Returned "
        "with `availability` on observation collections and omitted elsewhere.",
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
    """Links related to a list response."""

    self: str | None = Field(
        default=None,
        description="Link to this page of results. `null` when the API supplies none.",
    )


T = TypeVar("T")


class CollectionEnvelope(BaseModel, Generic[T]):
    """A page of results with its paging details and links."""

    data: list[T] = Field(description="The items on this page. Empty when there are none.")
    meta: CollectionMeta = Field(description="Paging, release, and availability details.")
    links: CollectionLinks = Field(description="Links related to this response.")

    @model_validator(mode="after")
    def validate_unavailable_data(self) -> "CollectionEnvelope[T]":
        if self.meta.availability == "UNAVAILABLE" and self.data:
            raise ValueError("UNAVAILABLE must not fabricate observation records")
        return self


class ResourceEnvelope(BaseModel, Generic[T]):
    """A single resource wrapped in `data`."""

    data: T = Field(description="The requested resource.")


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
