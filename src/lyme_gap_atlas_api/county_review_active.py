"""County Review v2: enabled assessment, conservative qualification, public evidence only."""

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime
from importlib.resources import files
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .cdc_surveillance import IDENTITIES, MEMBERS, PERIOD, RELEASE
from .county_review import (
    D_REASON,
    RULE_IDS,
    Candidate,
    CountyEvidence,
    DataGap,
    ReviewCoverage,
    ReviewEvidenceReference,
    RuleConfig,
    evaluate_d,
)
from .county_review import (
    StateReview as HistoricalStateReview,
)
from .models import CountyRecord
from .public_contract import (
    GeographyType,
    Observation,
    ObservationQuery,
    PublicQueryError,
    ValueState,
)
from .public_observations import ObservationService
from .repository import AtlasDataUnavailableError, Snapshot

RULE_ORDER = (
    "human_emerging",
    "vector_transition",
    "human_vector_discordance",
    "pathogen_present_vector_reported",
)


class ActiveReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    methodology_id: Literal["atlas-county-review"]
    methodology_version: Literal["2.0.0"]
    approval_reference: str = Field(min_length=1)
    rules: dict[str, RuleConfig]
    automatic_review_stage_assignment: Literal[False]

    @model_validator(mode="after")
    def validate_rules(self) -> "ActiveReviewConfig":
        if set(self.rules) != RULE_IDS or not all(r.enabled for r in self.rules.values()):
            raise ValueError("Version 2 requires all four assessment paths enabled")
        # Runtime parameters are deliberately unresolved. Do not accept arbitrary
        # expressions or make an editable config a scientific approval mechanism.
        expected = initial_rules()
        if self.rules != expected:
            raise ValueError("Unapproved version 2 rule configuration")
        return self


def initial_rules() -> dict[str, RuleConfig]:
    return {rule: RuleConfig(enabled=True) for rule in RULE_ORDER}


def load_active_config() -> tuple[ActiveReviewConfig, str]:
    raw = (
        files("lyme_gap_atlas_api")
        .joinpath("county-review-methodology-v2.config.json")
        .read_bytes()
    )
    return ActiveReviewConfig.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


class ReviewInput(BaseModel):
    """Existing public observation fields, or a published current county fact."""

    source_id: str
    source_label: str | None
    source_url: str | None
    source_vintage: str | None
    measure_id: str
    county_fips: str
    release_id: str
    public_record_ref: str
    value: float | str | None
    value_state: str
    period_start: date | None
    period_end: date | None
    temporal_grain: str
    retrieved_at: datetime | None
    methodology_version: str
    metadata_revision_id: str | None = None
    limitations: list[str]


class RuleResult(BaseModel):
    rule_id: str
    eligible: bool = False
    evaluated: bool = False
    abstained: bool = False
    candidate: bool = False
    reason_codes: list[str]
    detail: str
    evidence: list[ReviewInput] = Field(default_factory=list)
    _candidate: Candidate | None = PrivateAttr(default=None)


class CountyRuleResults(BaseModel):
    county_fips: str
    rules: list[RuleResult]


class RuleCounts(BaseModel):
    assessed_counties: int
    eligible_counties: int
    evaluated_counties: int
    abstained_counties: int
    candidate_counties: int


class StateReview(HistoricalStateReview):
    county_evaluations: list[CountyRuleResults] = Field(default_factory=list)
    rule_evaluation_counts: dict[str, RuleCounts] = Field(default_factory=dict)


ActiveStateReview = StateReview


class EvidenceInputs:
    def __init__(self, county: CountyRecord) -> None:
        self.county = county
        self.observations: list[Observation] = []
        self.published: list[ReviewInput] = []
        self.errors: list[str] = []
        self.native = CountyEvidence(county=county)


def observation_input(item: Observation) -> ReviewInput:
    return ReviewInput(
        source_id=item.source_id,
        source_label=item.source_label,
        source_url=item.source_url,
        source_vintage=item.source_vintage,
        measure_id=item.measure_id,
        county_fips=item.geography.geography_id,
        release_id=item.release_id,
        public_record_ref=item.provenance_ref,
        value=item.value,
        value_state=item.value_state,
        period_start=item.period_start,
        period_end=item.period_end,
        temporal_grain=item.temporal_grain,
        retrieved_at=item.atlas_acquired_at,
        methodology_version=item.methodology_version,
        metadata_revision_id=item.metadata_revision_id,
        limitations=item.limitations,
    )


def published_vector(county: CountyRecord, snapshot: Snapshot) -> list[ReviewInput]:
    """Published species facts can exclude D, never qualify a candidate or trend."""
    source = next((s for s in snapshot.metadata.sources if s.key == "tick"), None)
    if (
        source is None
        or source.vintage != "through 2025-12-31"
        or county.release_id != snapshot.metadata.release_id
    ):
        return []
    return [
        ReviewInput(
            source_id=source.key,
            source_label=source.label,
            source_url=source.url,
            source_vintage=source.vintage,
            measure_id=measure,
            county_fips=county.fips,
            release_id=county.release_id,
            public_record_ref=(
                f"/v1/counties/{county.fips}?dataset_version="
                f"{quote(snapshot.metadata.release_id, safe='')}"
            ),
            value=value,
            value_state="PUBLISHED_STATUS",
            period_start=None,
            period_end=PERIOD,
            temporal_grain="CUMULATIVE",
            retrieved_at=None,
            methodology_version=snapshot.metadata.methodology_version,
            limitations=[
                source.note,
                "Published county fact, not an admitted native observation. "
                "May exclude the current pairing; cannot qualify a candidate or trend.",
            ],
        )
        for measure, value in (
            ("scapularis_status", county.scapularis_status),
            ("pacificus_status", county.pacificus_status),
        )
        if value in {"Established", "Reported", "No records"}
    ]


class ReviewEvidenceLoader:
    """Reuse bounded ObservationService and the existing admitted CDC memberships."""

    def __init__(self, observations: ObservationService) -> None:
        self.observations = observations

    def load(self, state: str, snapshot: Snapshot) -> list[EvidenceInputs]:
        inputs = {c.fips: EvidenceInputs(c) for c in snapshot.counties if c.state == state}
        for item in inputs.values():
            item.published = published_vector(item.county, snapshot)
        fips = sorted(inputs)
        # 2023 is the explicitly published measure, not a chosen historical baseline.
        for start in range(0, len(fips), 200):
            group = fips[start : start + 200]
            self._load_query(
                ObservationQuery(
                    measure_id="case_count_floor_2023",
                    geography_type=GeographyType.county,
                    geography_id=group,
                    year=2023,
                    page_size=500,
                ),
                snapshot,
                inputs,
            )
        if self.observations.cdc_adapter is not None and snapshot.metadata.release_id == RELEASE:
            for measure, members in sorted(MEMBERS.items()):
                group = sorted(members & inputs.keys())
                if group:
                    self._load_query(
                        ObservationQuery(
                            measure_id=measure,
                            geography_type=GeographyType.county,
                            geography_id=group,
                            start_date=PERIOD,
                            end_date=PERIOD,
                            page_size=500,
                        ),
                        snapshot,
                        inputs,
                    )
        return [inputs[fips] for fips in sorted(inputs)]

    def _load_query(
        self, query: ObservationQuery, snapshot: Snapshot, inputs: dict[str, EvidenceInputs]
    ) -> None:
        query.validate_bounds(ceiling=500, annual=True)
        try:
            response = self.observations.search(query)
            rows = response.data
            if response.meta.next_page_token is not None:
                raise AtlasDataUnavailableError("Review evidence exceeds supported bounded slice")
            keys: set[tuple[str, str, date, date]] = set()
            for row in rows:
                key = (row.measure_id, row.geography.geography_id, row.period_start, row.period_end)
                expected_start = date(query.year, 1, 1) if query.year else query.start_date
                expected_end = date(query.year, 12, 31) if query.year else query.end_date
                if (
                    row.release_id != snapshot.metadata.release_id
                    or row.geography.geography_type != GeographyType.county
                    or row.geography.geography_id not in query.geography_id
                    or row.measure_id != query.measure_id
                    or row.period_start != expected_start
                    or row.period_end != expected_end
                    or key in keys
                ):
                    raise AtlasDataUnavailableError("Review evidence identity mismatch")
                keys.add(key)
            # Atomic admission: a mismatched row never leaves a partially accepted batch.
            for row in rows:
                inputs[row.geography.geography_id].observations.append(row)
                native_reference(row, inputs[row.geography.geography_id])
        except (AtlasDataUnavailableError, PublicQueryError):
            for fips in query.geography_id:
                inputs[fips].errors.append(f"{query.measure_id}:EVIDENCE_DELIVERY_UNAVAILABLE")


def native_reference(row: Observation, item: EvidenceInputs) -> None:
    """Bind only a projection admitted by the existing CDC adapter, never summaries."""
    if row.measure_id not in IDENTITIES or row.atlas_acquired_at is None:
        return
    dataset, version, metadata, source = IDENTITIES[row.measure_id]
    context = row.surveillance_context
    if (
        row.release_id != RELEASE
        or row.dataset_id != dataset
        or row.source_id != source
        or row.metadata_revision_id != metadata
        or context is None
        or context.evidence is None
        or row.temporal_grain != "CUMULATIVE"
        or row.period_start != PERIOD
        or row.period_end != PERIOD
    ):
        return
    ref = ReviewEvidenceReference(
        family="pathogen" if row.measure_id == "burgdorferi_status" else "vector",
        source_product=dataset,
        source_version=version,
        source_row_id=row.observation_id,
        revision_id=context.evidence.revision_id,
        public_record_ref=row.provenance_ref,
        county_fips=item.county.fips,
        release_id=row.release_id,
        source_as_of=PERIOD.isoformat(),
        retrieved_at=row.atlas_acquired_at,
        target="Borrelia burgdorferi sensu stricto"
        if row.measure_id == "burgdorferi_status"
        else "Ixodes scapularis",
        status=str(row.value),
        limitations=row.limitations,
    )
    if row.measure_id == "burgdorferi_status":
        item.native.pathogen = ref
    else:
        item.native.vector["Ixodes scapularis"] = ref


def abstain(rule: str, code: str, detail: str, evidence: list[ReviewInput]) -> RuleResult:
    return RuleResult(
        rule_id=rule,
        eligible=True,
        abstained=True,
        reason_codes=[code],
        detail=detail,
        evidence=evidence,
    )


def evaluate_human(item: EvidenceInputs) -> RuleResult:
    rule = "human_emerging"
    human = [r for r in item.observations if r.measure_id == "case_count_floor_2023"]
    refs = [observation_input(r) for r in human]
    if any(error.startswith("case_count_floor_2023:") for error in item.errors):
        return abstain(
            rule,
            "EVIDENCE_DELIVERY_UNAVAILABLE",
            "Human evidence delivery failed; this is not a scientific missing-value conclusion.",
            refs,
        )
    if any(r.source_id != "human" or r.dataset_id != "x5j9-wybp" for r in human):
        return abstain(
            rule, "SOURCE_NOT_APPROVED", "The published human source is incompatible.", refs
        )
    if not human or any(r.value_state not in {ValueState.OBSERVED, ValueState.ZERO} for r in human):
        return abstain(
            rule,
            "MISSING_HUMAN_SURVEILLANCE",
            "No usable published county human value; missing and suppression are not zero.",
            refs,
        )
    periods = {(r.period_start, r.period_end) for r in human}
    if len(periods) < 2:
        return abstain(
            rule,
            "NO_COMPARABLE_HISTORY",
            "Published 2023 floor is available; no admitted within-county baseline.",
            refs,
        )
    # The public human measure is an unbounded privacy floor, not an exact total.
    # More floor periods still cannot establish a direction for all consistent values.
    return abstain(
        rule,
        "FLOOR_CHANGE_NOT_IDENTIFIABLE",
        "No approved bounded floor comparator, lookback, support or freshness criterion.",
        refs,
    )


def evaluate_transition(item: EvidenceInputs) -> RuleResult:
    rule = "vector_transition"
    native = [
        r
        for r in item.observations
        if r.measure_id in {"scapularis_status", "pacificus_status", "burgdorferi_status"}
    ]
    refs = [observation_input(r) for r in native]
    if any(not error.startswith("case_count_floor_2023:") for error in item.errors):
        return abstain(
            rule,
            "EVIDENCE_DELIVERY_UNAVAILABLE",
            "Native vector/pathogen delivery failed; historical support is not determined.",
            refs,
        )
    same_measure_history = any(
        a.measure_id == b.measure_id and a.period_end != b.period_end
        for a in native
        for b in native
    )
    if not same_measure_history:
        return abstain(
            rule,
            "NO_COMPARABLE_NATIVE_SNAPSHOTS",
            "A current cumulative classification is not a historical transition. "
            "No admitted same-taxon/target snapshot pair is available.",
            refs + item.published,
        )
    if any(
        r.surveillance_context is None
        or r.surveillance_context.methodology_comparison is None
        or r.surveillance_context.methodology_comparison.comparison_state != "COMPARABLE"
        for r in native
    ):
        return abstain(
            rule,
            "COMPARABILITY_UNKNOWN",
            "Different snapshots require an admitted source/method comparability proof.",
            refs,
        )
    return abstain(
        rule,
        "TRANSITION_POLICY_UNAPPROVED",
        "No approved source-specific transition list, effort or freshness policy.",
        refs,
    )


def evaluate_discordance(item: EvidenceInputs) -> RuleResult:
    rule = "human_vector_discordance"
    refs = [observation_input(r) for r in item.observations]
    if item.errors:
        return abstain(
            rule,
            "EVIDENCE_DELIVERY_UNAVAILABLE",
            "Paired evidence delivery failed; a scientific difference cannot be assessed.",
            refs,
        )
    human = [
        r
        for r in item.observations
        if r.measure_id == "case_count_floor_2023" and r.value_state == ValueState.OBSERVED
    ]
    vector = [
        r
        for r in item.observations
        if r.measure_id in MEMBERS and r.value_state == ValueState.OBSERVED
    ]
    if not human or not vector:
        return abstain(
            rule,
            "INSUFFICIENT_PAIRED_EVIDENCE",
            "Both observed human and admitted vector/pathogen inputs are required; "
            "No records and missing values are not negative comparators.",
            refs,
        )
    if any(
        h.period_start != v.period_start
        or h.period_end != v.period_end
        or h.temporal_grain != v.temporal_grain
        for h in human
        for v in vector
    ):
        return abstain(
            rule,
            "INCOMPATIBLE_PERIODS",
            "Annual residence-based human floors and cumulative CDC statuses are not "
            "an approved aligned source pair.",
            refs,
        )
    return abstain(
        rule,
        "COMPARISON_CRITERION_UNAPPROVED",
        "Source pairing, sampling adequacy and material difference are not approved.",
        refs,
    )


def evaluate_pairing(item: EvidenceInputs) -> RuleResult:
    rule = "pathogen_present_vector_reported"
    if not item.county.in_contiguous_tick_scope:
        return RuleResult(
            rule_id=rule,
            reason_codes=["OUT_OF_SCOPE"],
            detail="Outside the declared CDC county scope.",
        )
    established = [ref for ref in item.published if ref.value == "Established"]
    if established:
        return RuleResult(
            rule_id=rule,
            eligible=True,
            evaluated=True,
            reason_codes=["VECTOR_ESTABLISHED_PAIRING_NOT_MET"],
            detail="Published species status is Established, so the neither-species-"
            "Established predicate is false. This does not evaluate emergence or risk.",
            evidence=established,
        )
    refs = [observation_input(r) for r in item.observations if r.measure_id in MEMBERS]
    if any(not error.startswith("case_count_floor_2023:") for error in item.errors):
        return abstain(
            rule,
            "EVIDENCE_DELIVERY_UNAVAILABLE",
            "Native pairing evidence delivery failed; source absence is not inferred.",
            refs + item.published,
        )
    evaluation = evaluate_d(
        item.native,
        RuleConfig(
            enabled=True,
            candidate_reason_code=D_REASON,
            source_products=["cdc-ixodes-county-status-2025", "cdc-ixodes-pathogen-status-2025"],
            vector_taxa=["Ixodes scapularis", "Ixodes pacificus"],
            pathogen_target="Borrelia burgdorferi sensu stricto",
            accepted_source_as_of="2025-12-31",
            eligible_source_versions={identity[0]: identity[1] for identity in IDENTITIES.values()},
            source_use_approval_reference=(
                "API #229 v2 execution; existing Data #429 admission required"
            ),
        ),
    )
    result = RuleResult(
        rule_id=rule,
        eligible=evaluation.eligible,
        evaluated=evaluation.evaluated,
        abstained=evaluation.abstained,
        candidate=evaluation.candidate is not None,
        reason_codes=(
            evaluation.candidate.reason_codes
            if evaluation.candidate
            else [g.code for g in evaluation.gaps] or ["PAIRING_NOT_MET"]
        ),
        detail=(
            evaluation.candidate.reason_text
            if evaluation.candidate
            else " ".join(g.detail for g in evaluation.gaps) or "The pairing predicate is not met."
        ),
        evidence=refs + item.published,
    )
    if evaluation.candidate:
        # Private ingestion source-version UUIDs are never public Review provenance.
        # v2 retains this legacy string field as the explicitly labeled source vintage.
        for ref in evaluation.candidate.evidence_references:
            ref.source_version = "cumulative-through-2025-12-31"
        result._candidate = evaluation.candidate
    return result


EVALUATORS: dict[str, Callable[[EvidenceInputs], RuleResult]] = {
    "human_emerging": evaluate_human,
    "vector_transition": evaluate_transition,
    "human_vector_discordance": evaluate_discordance,
    "pathogen_present_vector_reported": evaluate_pairing,
}


def evaluate_state(
    state: str,
    snapshot: Snapshot,
    inputs: list[EvidenceInputs],
    config: ActiveReviewConfig,
    digest: str,
    observation_context: str | None = None,
) -> ActiveStateReview:
    counties: list[CountyRuleResults] = []
    gaps: list[DataGap] = []
    for item in inputs:
        if item.county.state != state:
            continue
        if item.county.release_id != snapshot.metadata.release_id:
            raise AtlasDataUnavailableError("Review county release mismatch")
        results = []
        for rule in RULE_ORDER:
            result = EVALUATORS[rule](item)
            results.append(result)
            if result.abstained:
                gaps.append(
                    DataGap(
                        county_fips=item.county.fips,
                        code=result.reason_codes[0],
                        detail=f"{rule}: {result.detail}",
                    )
                )
        for error in sorted(set(item.errors)):
            gaps.append(
                DataGap(
                    county_fips=item.county.fips, code="EVIDENCE_DELIVERY_UNAVAILABLE", detail=error
                )
            )
        counties.append(CountyRuleResults(county_fips=item.county.fips, rules=results))
    counts = {
        rule: RuleCounts(
            assessed_counties=len(counties),
            eligible_counties=sum(
                r.eligible for c in counties for r in c.rules if r.rule_id == rule
            ),
            evaluated_counties=sum(
                r.evaluated for c in counties for r in c.rules if r.rule_id == rule
            ),
            abstained_counties=sum(
                r.abstained for c in counties for r in c.rules if r.rule_id == rule
            ),
            candidate_counties=sum(
                r.candidate for c in counties for r in c.rules if r.rule_id == rule
            ),
        )
        for rule in RULE_ORDER
    }
    candidates = [r._candidate for c in counties for r in c.rules if r._candidate is not None]
    abstained = sum(any(r.abstained for r in c.rules) for c in counties)
    evaluated = sum(any(r.evaluated for r in c.rules) for c in counties)
    return ActiveStateReview(
        requested_state=state,
        methodology_id=config.methodology_id,
        methodology_version=config.methodology_version,
        configuration_sha256=digest,
        data_release_version=snapshot.metadata.release_id,
        evaluated_at=datetime.now(UTC),
        requested_observation_context=observation_context,
        effective_observation_context=(
            "Published human floor 2023; cumulative CDC through 2025-12-31"
        ),
        result_state=(
            "candidates_found"
            if candidates
            else "insufficient_evidence"
            if abstained or not evaluated
            else "none_stand_out"
        ),
        review_candidates=candidates,
        data_gaps=gaps,
        coverage=ReviewCoverage(
            assessed_counties=len(counties),
            eligible_counties=sum(any(r.eligible for r in c.rules) for c in counties),
            evaluated_counties=evaluated,
            abstained_counties=abstained,
            rule_coverage={rule: "enabled" for rule in RULE_ORDER},
        ),
        limitations=[
            "Review is not disease risk. Enabled means the rule assesses inputs, not that evidence "
            "or scientific comparison criteria are sufficient to qualify a candidate.",
            "Missing, suppressed and No records are not negative observations.",
            "Current source admissions do not provide comparative history or a complete D pairing.",
            "Automatic review-stage assignment remains disabled.",
        ],
        county_evaluations=counties,
        rule_evaluation_counts=counts,
    )
