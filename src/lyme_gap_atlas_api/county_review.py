"""Versioned county Review evaluation over the governed current release."""

import hashlib
from datetime import UTC, datetime
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import CountyRecord
from .repository import Snapshot

RULE_IDS = frozenset(
    {
        "human_emerging",
        "vector_transition",
        "human_vector_discordance",
        "pathogen_present_vector_reported",
    }
)
D_REASON = "PATHOGEN_PRESENT_VECTOR_REPORTED_REVIEW"


class RuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    disabled_reason: str | None = None
    eligible_source_ids: list[str] | None = None
    eligible_methodology_eras: list[str] | None = None
    lookback_periods: int | None = Field(default=None, gt=0)
    minimum_count_or_support: int | None = Field(default=None, ge=0)
    freshness_limit: int | None = Field(default=None, gt=0)
    material_change_criterion: str | None = None
    allowed_evidence_state_transitions: list[str] | None = None
    minimum_effort_or_testing_support: int | None = Field(default=None, ge=0)
    snapshot_comparability_rule: str | None = None
    allowed_source_pairs: list[str] | None = None
    period_alignment_rule: str | None = None
    minimum_evidence_support: int | None = Field(default=None, ge=0)
    material_difference_criterion: str | None = None
    candidate_reason_code: str | None = None
    source_products: list[str] | None = None
    eligible_source_versions: dict[str, str] | None = None
    pathogen_target: str | None = None
    vector_taxa: list[str] | None = None
    accepted_source_as_of: str | None = None
    source_use_approval_reference: str | None = None


class ReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    methodology_id: Literal["atlas-county-review"]
    methodology_version: Literal["1.0.0"]
    configuration_role: Literal["canonical_initial_methodology_definition_for_api_93"]
    rules: dict[str, RuleConfig]
    automatic_review_stage_assignment: Literal[False]

    @model_validator(mode="after")
    def validate_rules(self) -> "ReviewConfig":
        if set(self.rules) != RULE_IDS:
            raise ValueError("Unknown or missing Review rule")
        if any(rule.enabled for rule in self.rules.values()):
            raise ValueError("Method 1.0.0 has no approved enabled rules")
        d = self.rules["pathogen_present_vector_reported"]
        if (
            d.candidate_reason_code != D_REASON
            or d.source_products
            != ["cdc-ixodes-county-status-2025", "cdc-ixodes-pathogen-status-2025"]
            or d.pathogen_target != "Borrelia burgdorferi sensu stricto"
            or d.vector_taxa != ["Ixodes scapularis", "Ixodes pacificus"]
            or d.accepted_source_as_of != "2025-12-31"
            or d.eligible_source_versions is not None
            or d.source_use_approval_reference is not None
        ):
            raise ValueError("Unapproved D source or predicate configuration")
        return self


def load_config() -> tuple[ReviewConfig, str]:
    raw = (
        files("lyme_gap_atlas_api")
        .joinpath("county-review-methodology-v1.config.json")
        .read_bytes()
    )
    return ReviewConfig.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


class ReviewEvidenceReference(BaseModel):
    family: str
    source_product: str
    source_version: str
    source_row_id: str
    revision_id: str
    county_fips: str
    release_id: str
    source_as_of: str
    retrieved_at: datetime
    target: str
    status: str
    limitations: list[str] = Field(default_factory=list)


class CountyEvidence(BaseModel):
    county: CountyRecord
    vector: dict[str, ReviewEvidenceReference] = Field(default_factory=dict)
    pathogen: ReviewEvidenceReference | None = None
    diagnostics: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    county_fips: str
    county_name: str
    county_url: str
    reason_codes: list[str]
    reason_text: str
    evidence_families: list[str]
    evidence_references: list[ReviewEvidenceReference]
    completeness: Literal["limited"] = "limited"
    limitations: list[str]
    freshness_comparability: str
    suggested_next_check: str


class DataGap(BaseModel):
    county_fips: str
    code: str
    detail: str


class CountyEvaluation(BaseModel):
    candidate: Candidate | None = None
    gaps: list[DataGap] = Field(default_factory=list)
    abstained: bool = False
    eligible: bool = False
    evaluated: bool = False


class ReviewCoverage(BaseModel):
    assessed_counties: int
    eligible_counties: int
    abstained_counties: int
    evaluated_counties: int
    rule_coverage: dict[str, str]


class StateReview(BaseModel):
    requested_state: str
    methodology_id: str
    methodology_version: str
    configuration_sha256: str
    data_release_version: str
    evaluated_at: datetime
    requested_observation_context: str | None
    effective_observation_context: str
    result_state: Literal[
        "candidates_found", "insufficient_evidence", "unsupported", "none_stand_out"
    ]
    review_candidates: list[Candidate]
    data_gaps: list[DataGap]
    coverage: ReviewCoverage
    limitations: list[str]


def evaluate_d(evidence: CountyEvidence, rule: RuleConfig) -> CountyEvaluation:
    """Evaluate D only on exact native rows. Never use aggregate tick_status."""
    county = evidence.county
    products = rule.source_products or []
    taxa = rule.vector_taxa or []
    versions = rule.eligible_source_versions or {}
    if (
        len(products) != 2
        or len(taxa) != 2
        or set(versions) != set(products)
        or not all(versions.values())
        or not rule.source_use_approval_reference
    ):
        return CountyEvaluation(
            abstained=True,
            gaps=[
                DataGap(
                    county_fips=county.fips,
                    code="RULE_CONFIGURATION_UNAPPROVED",
                    detail="Exact source versions and use approval are required.",
                )
            ],
        )
    if not county.in_contiguous_tick_scope:
        return CountyEvaluation(
            gaps=[
                DataGap(
                    county_fips=county.fips,
                    code="OUT_OF_SCOPE",
                    detail="County is outside the approved source geography.",
                )
            ]
        )
    refs = [
        evidence.pathogen,
        evidence.vector.get(taxa[0]),
        evidence.vector.get(taxa[1]),
    ]
    if evidence.diagnostics:
        return CountyEvaluation(
            eligible=True,
            abstained=True,
            gaps=[
                DataGap(
                    county_fips=county.fips,
                    code=code,
                    detail="Source evidence cannot be safely evaluated.",
                )
                for code in evidence.diagnostics
            ],
        )
    if any(ref is None for ref in refs):
        return CountyEvaluation(
            eligible=True,
            abstained=True,
            gaps=[
                DataGap(
                    county_fips=county.fips,
                    code="MISSING_SOURCE_EVIDENCE",
                    detail="Required source-native county status or lineage is unavailable.",
                )
            ],
        )
    pathogen, scapularis, pacificus = refs
    assert pathogen is not None and scapularis is not None and pacificus is not None
    valid = (
        pathogen.source_product == products[1]
        and scapularis.source_product == pacificus.source_product == products[0]
        and pathogen.target == rule.pathogen_target
        and scapularis.target == taxa[0]
        and pacificus.target == taxa[1]
        and all(
            ref.county_fips == county.fips
            and ref.release_id == county.release_id
            and ref.source_as_of == rule.accepted_source_as_of
            and ref.source_version == versions[ref.source_product]
            and ref.source_row_id
            and ref.revision_id
            for ref in (pathogen, scapularis, pacificus)
        )
    )
    if not valid:
        return CountyEvaluation(
            eligible=True,
            abstained=True,
            gaps=[
                DataGap(
                    county_fips=county.fips,
                    code="SOURCE_PROVENANCE_MISMATCH",
                    detail="Source identity, revision, geography, or release is unapproved.",
                )
            ],
        )
    if pathogen.status not in {"Present", "No records"} or any(
        ref.status not in {"Established", "Reported", "No records"}
        for ref in (scapularis, pacificus)
    ):
        return CountyEvaluation(
            eligible=True,
            abstained=True,
            gaps=[
                DataGap(
                    county_fips=county.fips,
                    code="SOURCE_SEMANTICS_UNKNOWN",
                    detail="A source status is unknown or unsupported.",
                )
            ],
        )
    if pathogen.status == "No records":
        return CountyEvaluation(
            eligible=True,
            abstained=True,
            gaps=[
                DataGap(
                    county_fips=county.fips,
                    code="NO_QUALIFYING_PATHOGEN_RECORD",
                    detail="No records is not a sampled negative or pathogen absence.",
                )
            ],
        )
    if "Established" in {scapularis.status, pacificus.status}:
        return CountyEvaluation(eligible=True, evaluated=True)
    if "Reported" not in {scapularis.status, pacificus.status}:
        return CountyEvaluation(
            eligible=True,
            abstained=True,
            gaps=[
                DataGap(
                    county_fips=county.fips,
                    code="NO_QUALIFYING_VECTOR_EVIDENCE",
                    detail="No species is Reported; No records is not absence.",
                )
            ],
        )
    return CountyEvaluation(
        eligible=True,
        evaluated=True,
        candidate=Candidate(
            county_fips=county.fips,
            county_name=county.county,
            county_url=f"/v1/counties/{county.fips}",
            reason_codes=[D_REASON],
            reason_text=(
                "CDC records B. burgdorferi in host-seeking ticks while county "
                "Ixodes status is Reported; inspect the source records."
            ),
            evidence_families=["county_pathogen_status", "county_vector_status"],
            evidence_references=[pathogen, scapularis, pacificus],
            limitations=[
                "Cumulative status does not establish timing, transmission, outbreak, or risk.",
                "Effort, collection date, and pathogen species attribution are unavailable.",
            ],
            freshness_comparability="Pinned source date and release; recency unknown.",
            suggested_next_check="Review CDC records and local tick surveillance history.",
        ),
    )


def aggregate_state(
    state: str,
    snapshot: Snapshot,
    evidence: list[CountyEvidence],
    config: ReviewConfig,
    config_hash: str,
    observation_context: str | None = None,
) -> StateReview:
    counties = [item for item in evidence if item.county.state == state]
    evaluations = (
        [evaluate_d(item, config.rules["pathogen_present_vector_reported"]) for item in counties]
        if config.rules["pathogen_present_vector_reported"].enabled
        else []
    )
    enabled = [key for key, rule in config.rules.items() if rule.enabled]
    candidates = [result.candidate for result in evaluations if result.candidate is not None]
    gaps = [gap for result in evaluations for gap in result.gaps]
    if not enabled:
        gaps = [
            DataGap(
                county_fips=item.county.fips,
                code="SOURCE_NATIVE_LINEAGE_UNAVAILABLE",
                detail="Summary statuses lack exact source rows and revisions.",
            )
            for item in counties
            if item.county.in_contiguous_tick_scope
        ]
    state_result: Literal[
        "candidates_found", "insufficient_evidence", "unsupported", "none_stand_out"
    ]
    if candidates:
        state_result = "candidates_found"
    elif not enabled:
        state_result = "unsupported"
    elif any(result.abstained for result in evaluations) or not any(
        result.evaluated for result in evaluations
    ):
        state_result = "insufficient_evidence"
    else:
        state_result = "none_stand_out"
    return StateReview(
        requested_state=state,
        methodology_id=config.methodology_id,
        methodology_version=config.methodology_version,
        configuration_sha256=config_hash,
        data_release_version=snapshot.metadata.release_id,
        evaluated_at=datetime.now(UTC),
        requested_observation_context=observation_context,
        effective_observation_context="Current cumulative county status; human snapshot 2023",
        result_state=state_result,
        review_candidates=candidates,
        data_gaps=gaps,
        coverage=ReviewCoverage(
            assessed_counties=len(counties),
            eligible_counties=sum(r.eligible for r in evaluations) if enabled else 0,
            abstained_counties=sum(r.abstained for r in evaluations) if enabled else 0,
            evaluated_counties=sum(r.evaluated for r in evaluations) if enabled else 0,
            rule_coverage={
                key: "enabled" if rule.enabled else "disabled" for key, rule in config.rules.items()
            },
        ),
        limitations=["Review is not disease risk; no candidate rule is approved in 1.0.0."]
        if not enabled
        else [],
    )
