"""Structured Ask Atlas orchestration over the registered, request-local #18 tools.

The answer text is assembled from validated governed fields. No model has an
opportunity to supply a tool name, query, value, or uncited substantive claim.
"""

import json
import re
from datetime import date, datetime
from typing import Any, Literal, Protocol

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .ask_atlas_tools import Coverage, MetadataItem, ToolResult
from .assistant_policy import load_assistant_policy
from .public_contract import (
    EvidenceReference,
    GeographyIdentity,
    GeographyType,
    Measure,
    Observation,
    ValueState,
)

ROUTING_VERSION = "ask-atlas-structured-routing-v1"
ANSWER_VERSION = "ask-atlas-v1"
_UNSAFE = re.compile(
    r"\b(select\s+.+\s+from|insert\s+into|delete\s+from|update\s+.+\s+set|"
    r"match\s*\(|cypher|sql|warehouse|repository|diagnos\w*|prescri\w*|"
    r"treat(?:ment)?\s+(?:me|my|this patient)|antibiotic\w*|"
    r"tick bite|medical advice|therap\w*|dose|medication)\b",
    re.IGNORECASE,
)
_UNSUPPORTED = re.compile(
    r"\b(predict\w*|forecast\w*|caus\w*|risk|monthly|strat\w*|"
    r"by age|by sex|child(?:ren)?|pediatric\w*|aggregate|sum the counties|"
    r"official determination)\b",
    re.IGNORECASE,
)
_FIPS = re.compile(r"(?<!\d)\d{5}(?!\d)")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_MEASURE_REFERENT = re.compile(
    r"\b(case|cases|count|counts|value|values|observation|observations|"
    r"measure|row|rows|coverage|evidence|source|provenance|freshness|"
    r"current|stale)\b",
    re.IGNORECASE,
)
_FOREIGN_MEASURE = re.compile(
    r"\b(population|weather|temperature|precipitation|rainfall|income|"
    r"hospitalization|mortality)\b",
    re.IGNORECASE,
)
_CONTEXT_PLACE = re.compile(
    r"\b(?:this|selected|the|same|cited|governed)\s+"
    r"(?:county|counties|observation|row|measure)\b",
    re.IGNORECASE,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StructuredContext(StrictModel):
    measure_id: str | None = Field(default=None, min_length=1, max_length=120)
    indicator_id: str | None = Field(default=None, min_length=1, max_length=120)
    search_text: str | None = Field(default=None, min_length=1, max_length=120)
    geography_type: Literal["county", "state"] = "county"
    geography_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)
    year: int | None = Field(default=None, ge=1900, le=2100)
    start_date: date | None = None
    end_date: date | None = None
    expected_observation_id: str | None = Field(default=None, min_length=1, max_length=200)


class StructuredAssistantRequest(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    source_mode: Literal["Literature", "Structured", "Both"] = "Structured"
    context: StructuredContext = Field(default_factory=StructuredContext)


class Claim(StrictModel):
    claim_id: str
    text: str = Field(min_length=1)
    structured_refs: list[EvidenceReference] = Field(default_factory=list)
    measure_ids: list[str] = Field(default_factory=list)
    coverage_ids: list[str] = Field(default_factory=list)
    literature_citation_ids: list[str] = Field(default_factory=list)
    role: Literal["finding", "comparison_limitation"] = "finding"


class Freshness(StrictModel):
    observation_id: str
    state: Literal["current", "stale", "unknown"] = "unknown"
    policy_id: str | None = None
    source_timestamp: datetime | None = None
    compared_at: datetime | None = None

    @model_validator(mode="after")
    def governed_policy_required(self) -> "Freshness":
        if self.state in {"current", "stale"} and not (
            self.policy_id and self.source_timestamp and self.compared_at
        ):
            raise ValueError("current or stale requires a governed policy and timestamp")
        if any(
            stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None)
            for stamp in (self.source_timestamp, self.compared_at)
        ):
            raise ValueError("freshness timestamps require UTC offsets")
        return self


class Replay(StrictModel):
    provider_id: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    tool_contract_version: Literal["ask-atlas-v1"] = "ask-atlas-v1"
    routing_policy_version: str = ROUTING_VERSION
    configuration_version: str | None = None
    assistant_policy_version: str | None = None
    release_id: str | None = None
    literature_retrieval_configuration_version: str | None = None


class Answer(StrictModel):
    kind: Literal["answer"] = "answer"
    contract_version: Literal["ask-atlas-v1"] = "ask-atlas-v1"
    requested_source_mode: Literal["Literature", "Structured", "Both"]
    actual_sources_used: list[Literal["structured_atlas", "literature_evidence"]] = Field(
        default_factory=list
    )
    outcome: Literal[
        "ANSWERED",
        "NEEDS_CLARIFICATION",
        "INSUFFICIENT_EVIDENCE",
        "SOURCE_UNAVAILABLE",
        "UNSUPPORTED_REQUEST",
        "SAFETY_REFUSAL",
        "QUERY_TOO_BROAD",
    ]
    claims: list[Claim] = Field(default_factory=list)
    structured_evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    literature_citations: list[dict[str, Any]] = Field(default_factory=list)
    cross_source_state: None = None
    freshness: list[Freshness] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    replay: Replay = Field(default_factory=Replay)
    structured_measures: list[Measure] = Field(default_factory=list)
    structured_coverage: list[Coverage] = Field(default_factory=list)
    literature_evidence_state: None = None

    @model_validator(mode="after")
    def grounded(self) -> "Answer":
        refs = {
            json.dumps(r.model_dump(mode="json"), sort_keys=True)
            for r in self.structured_evidence_refs
        }
        measures = {m.measure_id for m in self.structured_measures}
        coverage = {c.coverage_id for c in self.structured_coverage}
        claim_ids = [c.claim_id for c in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("duplicate claim identity")
        for claim in self.claims:
            if claim.role != "finding" or claim.literature_citation_ids:
                raise ValueError("structured answer cannot admit literature or mixed claims")
            if not (claim.structured_refs or claim.measure_ids or claim.coverage_ids):
                raise ValueError("uncited substantive claim")
            if any(
                json.dumps(ref.model_dump(mode="json"), sort_keys=True) not in refs
                for ref in claim.structured_refs
            ):
                raise ValueError("claim cites an unadmitted observation")
            if not set(claim.measure_ids) <= measures or not set(claim.coverage_ids) <= coverage:
                raise ValueError("claim cites unadmitted measure or coverage")
        if self.outcome == "ANSWERED" and not self.claims:
            raise ValueError("answered response has no grounded claims")
        if self.outcome != "ANSWERED" and self.claims:
            raise ValueError("non-answer cannot contain finding claims")
        if self.actual_sources_used != (["structured_atlas"] if self.claims else []):
            raise ValueError("source badge must follow cited claims")
        if self.replay.release_id is None and self.claims:
            raise ValueError("grounded structured claims need a release")
        if not self.claims and (
            self.structured_evidence_refs
            or self.structured_measures
            or self.structured_coverage
            or self.replay.release_id is not None
        ):
            raise ValueError("unused structured evidence cannot be admitted")
        if any(ref.release_id != self.replay.release_id for ref in self.structured_evidence_refs):
            raise ValueError("observation release mismatch")
        if any(m.release_version != self.replay.release_id for m in self.structured_measures):
            raise ValueError("measure release mismatch")
        if any(c.release_id != self.replay.release_id for c in self.structured_coverage):
            raise ValueError("coverage release mismatch")
        return self


class StructuredAssistantResponse(StrictModel):
    answer: Answer
    tool_evidence: list[ToolResult] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def claims_match_tool_evidence(self) -> "StructuredAssistantResponse":
        observations = {
            obs.observation_id: obs
            for result in self.tool_evidence
            if result.tool == "get_observations" and result.status == "ok"
            for obs in result.observations
        }
        measures = {
            measure.measure_id: measure
            for result in self.tool_evidence
            if result.tool == "find_measures" and result.status == "ok"
            for measure in result.measures
        }
        coverage = {
            row.coverage_id: row
            for result in self.tool_evidence
            if result.tool == "get_observations" and result.status == "ok"
            for row in result.coverage
        }
        metadata = {
            item.observation_id: item
            for result in self.tool_evidence
            if result.tool == "get_evidence_metadata" and result.status == "ok"
            for item in result.metadata
        }
        cited_observations: set[str] = set()
        for claim in self.answer.claims:
            expected: str | None = None
            if claim.claim_id.startswith("atlas:observation:") and len(claim.structured_refs) == 1:
                identifier = claim.claim_id.removeprefix("atlas:observation:")
                obs = observations.get(identifier)
                item = metadata.get(identifier)
                if obs and claim.structured_refs == [obs.evidence]:
                    if item and (
                        item.source.source_id != obs.source_id
                        or item.source.release_version != obs.release_id
                        or (
                            item.methodology
                            and (
                                item.methodology.methodology_id != obs.methodology_id
                                or item.methodology.measure_id != obs.measure_id
                                or item.methodology.release_version != obs.release_id
                            )
                        )
                    ):
                        raise ValueError("metadata is inconsistent with cited observation")
                    expected = _render_observation(obs, item)
                    cited_observations.add(identifier)
                    required_limits = (
                        obs.limitations
                        + (item.source.limitations if item else [])
                        + (item.methodology.limitations if item and item.methodology else [])
                    )
                    if not set(required_limits) <= set(self.answer.limitations):
                        raise ValueError("governed limitations were dropped")
            elif claim.claim_id.startswith("atlas:measure:") and len(claim.measure_ids) == 1:
                identifier = claim.claim_id.removeprefix("atlas:measure:")
                measure = measures.get(identifier)
                if measure and claim.measure_ids == [identifier]:
                    expected = _render_measure(measure)
            elif claim.claim_id == "atlas:comparison" and len(claim.structured_refs) == 2:
                left, right = claim.structured_refs
                first = observations.get(left.resource_id)
                second = observations.get(right.resource_id)
                if first and second and claim.structured_refs == [first.evidence, second.evidence]:
                    expected = _render_comparison(first, second)
                    cited_observations.update((first.observation_id, second.observation_id))
                    if not set(first.limitations + second.limitations) <= set(
                        self.answer.limitations
                    ):
                        raise ValueError("governed limitations were dropped")
            elif claim.claim_id.startswith("atlas:coverage:") and len(claim.coverage_ids) == 1:
                identifier = claim.claim_id.removeprefix("atlas:coverage:")
                row = coverage.get(identifier)
                if row and claim.coverage_ids == [identifier]:
                    expected = _render_coverage(row)
            if claim.text != expected:
                raise ValueError("claim text is not generated from cited tool evidence")
        if {item.observation_id for item in self.answer.freshness} != cited_observations:
            raise ValueError("freshness record missing for cited observation")
        return self


def _render_measure(measure: Measure) -> str:
    return (
        f"The governed measure {measure.measure_id} is {measure.label}; "
        f"unit: {measure.unit or 'not supplied'} (release {measure.release_version})."
    )


def _render_observation(obs: Observation, metadata: MetadataItem | None = None) -> str:
    prefix = f"County {obs.geography.geography_id}, {obs.period_start} to {obs.period_end}, "
    if metadata is not None:
        method = (
            f"{metadata.methodology.methodology_id} version {metadata.methodology.version}"
            if metadata.methodology
            else "no governed methodology resource"
        )
        return (
            f"{prefix}{obs.measure_id} comes from {metadata.source.label} "
            f"({metadata.source.source_id}; "
            f"publisher: {metadata.source.publisher or 'not supplied'}), "
            f"method: {method}; transformation version {obs.methodology_version}, "
            f"release {obs.release_id}."
        )
    value = (
        f"{obs.value} {obs.unit} ({obs.value_state.value})"
        if obs.value_state in {ValueState.ZERO, ValueState.OBSERVED}
        else f"{obs.value_state.value}; no numeric value"
    )
    return (
        f"{prefix}{obs.measure_id} is {value}; source {obs.source_id}, "
        f"methodology version {obs.methodology_version}, release {obs.release_id}."
    )


def _render_coverage(row: Coverage) -> str:
    prefix = "A governed row is present" if row.state == "present" else "No governed row is present"
    text = (
        f"{prefix} for county {row.geography.geography_id}, "
        f"{row.period_start} to {row.period_end}, measure {row.measure_id}, "
        f"release {row.release_id}."
    )
    return text if row.state == "present" else text + " This is not a zero value."


def _render_comparison(first: Observation, second: Observation) -> str:
    if (
        first.measure_id != second.measure_id
        or first.period_start != second.period_start
        or first.period_end != second.period_end
        or first.unit != second.unit
        or first.denominator != second.denominator
        or first.release_id != second.release_id
        or first.geography == second.geography
        or first.value_state not in {ValueState.ZERO, ValueState.OBSERVED}
        or second.value_state not in {ValueState.ZERO, ValueState.OBSERVED}
    ):
        raise ValueError("observations are not comparable")
    return (
        f"For {first.measure_id}, {first.period_start} to {first.period_end}, "
        f"county {first.geography.geography_id} has {first.value} {first.unit} "
        f"({first.value_state.value}) and county {second.geography.geography_id} "
        f"has {second.value} {second.unit} ({second.value_state.value}); "
        f"release {first.release_id}. These are governed observations, not a causal comparison."
    )


class StructuredToolPort(Protocol):
    def find_measures(self, raw: dict[str, Any]) -> ToolResult: ...
    def get_observations(self, raw: dict[str, Any]) -> ToolResult: ...
    def get_evidence_metadata(self, raw: dict[str, Any]) -> ToolResult: ...
    def verify_release(self) -> str: ...


def _intent(question: str) -> str | None:
    text = question.casefold()
    if any(word in text for word in ("current", "fresh", "stale")):
        return "freshness"
    if any(word in text for word in ("where did", "source", "methodology", "provenance")):
        return "provenance"
    if any(word in text for word in ("which governed measure", "find measure", "what measure")):
        return "discovery"
    if any(word in text for word in ("lacks", "lack ", "have the governed", "coverage", "no row")):
        return "gap"
    if "compare" in text:
        return "comparison"
    if any(word in text for word in ("what is", "give", "show", "is the", "how many")):
        return "observation"
    return None


def _error_outcome(code: str | None) -> str:
    if code == "QUERY_TOO_BROAD":
        return "QUERY_TOO_BROAD"
    if code in {
        "INVALID_REQUEST",
        "UNSUPPORTED_FILTER",
        "UNSUPPORTED_STRATIFICATION",
        "RESOURCE_NOT_FOUND",
    }:
        return "UNSUPPORTED_REQUEST"
    return "SOURCE_UNAVAILABLE"


class StructuredAssistant:
    """One bounded request: optional discovery, one observation call, optional metadata."""

    def __init__(self, tools: StructuredToolPort) -> None:
        self.tools = tools

    def ask(self, request: StructuredAssistantRequest) -> StructuredAssistantResponse:
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(
            "atlas.ask_atlas.structured", record_exception=False, set_status_on_exception=False
        ) as span:
            response = self._ask(request)
            span.set_attribute("atlas.ask_atlas.source_mode", request.source_mode)
            span.set_attribute("atlas.ask_atlas.outcome", response.answer.outcome)
            span.set_attribute("atlas.ask_atlas.tool_count", len(response.tool_evidence))
            span.set_attribute("atlas.ask_atlas.claim_count", len(response.answer.claims))
            return response

    def _ask(self, request: StructuredAssistantRequest) -> StructuredAssistantResponse:
        evidence: list[ToolResult] = []
        context = request.context

        def finish(
            outcome: str,
            *,
            claims: list[Claim] | None = None,
            refs: list[EvidenceReference] | None = None,
            measures: list[Measure] | None = None,
            coverage: list[Coverage] | None = None,
            freshness: list[Freshness] | None = None,
            limitations: list[str] | None = None,
            release: str | None = None,
        ) -> StructuredAssistantResponse:
            admitted = claims or []
            answer = Answer(
                requested_source_mode=request.source_mode,
                outcome=outcome,  # type: ignore[arg-type]
                claims=admitted,
                actual_sources_used=["structured_atlas"] if admitted else [],
                structured_evidence_refs=refs or [],
                structured_measures=measures or [],
                structured_coverage=coverage or [],
                freshness=freshness or [],
                limitations=limitations or [],
                replay=Replay(
                    release_id=release,
                    assistant_policy_version=load_assistant_policy().version,
                ),
            )
            return StructuredAssistantResponse(answer=answer, tool_evidence=evidence)

        question = request.question.strip()
        if _UNSAFE.search(question):
            return finish(
                "SAFETY_REFUSAL",
                limitations=["This request is outside bounded Atlas evidence access."],
            )
        if request.source_mode != "Structured":
            return finish(
                "SOURCE_UNAVAILABLE",
                limitations=["This source mode is not enabled on the structured endpoint."],
            )
        if _UNSUPPORTED.search(question):
            return finish(
                "UNSUPPORTED_REQUEST",
                limitations=["The requested interpretation or filter is unsupported."],
            )
        intent = _intent(question)
        if intent is None:
            return finish(
                "UNSUPPORTED_REQUEST",
                limitations=["This question is outside supported Structured question classes."],
            )
        if intent == "discovery":
            selector: dict[str, Any]
            if context.indicator_id is not None and context.search_text is None:
                selector = {"indicator_id": context.indicator_id}
            elif context.search_text is not None and context.indicator_id is None:
                selector = {"search_text": context.search_text}
            else:
                return finish(
                    "NEEDS_CLARIFICATION",
                    limitations=["Specify a governed indicator ID or measure search text."],
                )
            result = self._call("find_measures", selector)
            evidence.append(result)
            if result.status == "error":
                return finish(_error_outcome(result.error_code))
            if not self._valid(result, "find_measures") or not self._same_release(
                result.release_id
            ):
                return finish(
                    "SOURCE_UNAVAILABLE",
                    limitations=["Governed measure evidence could not be validated."],
                )
            if len(result.measures) != 1:
                return finish(
                    "NEEDS_CLARIFICATION",
                    limitations=["Select one of the governed matching measures."],
                )
            measure_claims = [
                Claim(
                    claim_id=f"atlas:measure:{m.measure_id}",
                    text=_render_measure(m),
                    measure_ids=[m.measure_id],
                )
                for m in result.measures
            ]
            return finish(
                "ANSWERED",
                claims=measure_claims,
                measures=result.measures,
                release=result.release_id,
            )

        if (
            not context.measure_id
            or not context.geography_ids
            or (context.year is None and (context.start_date is None or context.end_date is None))
        ):
            return finish(
                "NEEDS_CLARIFICATION",
                limitations=["Specify a measure, county FIPS, and complete annual period."],
            )
        if context.geography_type != "county":
            return finish(
                "UNSUPPORTED_REQUEST",
                limitations=["Only governed county observations are available."],
            )
        if context.year is not None and (
            context.start_date is not None or context.end_date is not None
        ):
            return finish(
                "UNSUPPORTED_REQUEST",
                limitations=["Use either a year or a complete whole-year range."],
            )
        if context.year is None and (
            context.start_date is None
            or context.end_date is None
            or context.start_date > context.end_date
            or (context.start_date.month, context.start_date.day) != (1, 1)
            or (context.end_date.month, context.end_date.day) != (12, 31)
        ):
            return finish(
                "UNSUPPORTED_REQUEST",
                limitations=["Only complete inclusive annual periods are supported."],
            )
        try:
            identities = [
                GeographyIdentity(geography_type=GeographyType.county, geography_id=geo)
                for geo in context.geography_ids
            ]
        except ValidationError:
            return finish("UNSUPPORTED_REQUEST", limitations=["Invalid county FIPS."])
        if len({g.geography_id for g in identities}) != len(identities):
            return finish("UNSUPPORTED_REQUEST", limitations=["County FIPS must be unique."])
        mentioned_fips = set(_FIPS.findall(question))
        if mentioned_fips and mentioned_fips != set(context.geography_ids):
            return finish(
                "UNSUPPORTED_REQUEST", limitations=["Question and requested county scope differ."]
            )
        mentioned_years = {int(year) for year in _YEAR.findall(question)}
        requested_years = (
            {context.year}
            if context.year is not None
            else set(range(context.start_date.year, context.end_date.year + 1))  # type: ignore[union-attr]
        )
        if len(requested_years) * len(context.geography_ids) > 200:
            return finish(
                "QUERY_TOO_BROAD",
                limitations=["The requested county/year product exceeds 200 slots."],
            )
        if mentioned_years and not mentioned_years <= requested_years:
            return finish(
                "UNSUPPORTED_REQUEST", limitations=["Question and requested period differ."]
            )
        if intent == "comparison" and (
            len(context.geography_ids) != 2 or len(requested_years) != 1
        ):
            return finish(
                "NEEDS_CLARIFICATION",
                limitations=["Select two counties and one common annual period."],
            )
        # Context selects a slot; it cannot silently replace the subject or place
        # of the user's question. Unrecognized wording needs explicit clarification.
        if _FOREIGN_MEASURE.search(question) or not _MEASURE_REFERENT.search(question):
            return finish(
                "NEEDS_CLARIFICATION",
                limitations=["Confirm the requested governed measure in the question."],
            )
        if not mentioned_fips and not _CONTEXT_PLACE.search(question):
            return finish(
                "NEEDS_CLARIFICATION",
                limitations=["Confirm the selected county FIPS in the question."],
            )
        query: dict[str, Any] = {
            "measure_id": context.measure_id,
            "geography_type": context.geography_type,
            "geography_ids": context.geography_ids,
        }
        if context.year is not None:
            query["year"] = context.year
        else:
            query["start_date"] = context.start_date.isoformat() if context.start_date else None
            query["end_date"] = context.end_date.isoformat() if context.end_date else None
        result = self._call("get_observations", query)
        evidence.append(result)
        if result.status == "error":
            return finish(_error_outcome(result.error_code))
        if (
            not self._valid(result, "get_observations")
            or not self._valid_observation_scope(result, query, requested_years)
            or not self._same_release(result.release_id)
        ):
            return finish(
                "SOURCE_UNAVAILABLE", limitations=["Governed observations could not be validated."]
            )
        if context.expected_observation_id and context.expected_observation_id not in {
            obs.observation_id for obs in result.observations
        }:
            return finish(
                "UNSUPPORTED_REQUEST",
                limitations=["The cited observation is no longer in the current release."],
            )
        if intent in {"observation", "comparison"}:
            absent = [row for row in result.coverage if row.state == "absent"]
            if absent:
                return finish(
                    "INSUFFICIENT_EVIDENCE",
                    limitations=[
                        "No governed observation exists for "
                        f"county {row.geography.geography_id} in {row.period_start.year}; "
                        "the numeric request cannot be answered for every selected slot."
                        for row in absent
                    ],
                )
        metadata: ToolResult | None = None
        if intent == "provenance":
            ids = [obs.observation_id for obs in result.observations]
            if not ids:
                return finish("INSUFFICIENT_EVIDENCE")
            if len(ids) > 20:
                return finish("QUERY_TOO_BROAD")
            metadata = self._call("get_evidence_metadata", {"observation_ids": ids})
            evidence.append(metadata)
            if metadata.status == "error":
                return finish(_error_outcome(metadata.error_code))
            if (
                not self._valid(metadata, "get_evidence_metadata", result.release_id)
                or {item.observation_id for item in metadata.metadata} != set(ids)
                or len(metadata.metadata) != len(ids)
                or any(
                    item.source.source_id != obs.source_id
                    or ((item.methodology is None) != (obs.methodology_id is None))
                    or (
                        item.methodology is not None
                        and (
                            item.methodology.methodology_id != obs.methodology_id
                            or item.methodology.measure_id != obs.measure_id
                        )
                    )
                    for item in metadata.metadata
                    for obs in result.observations
                    if item.observation_id == obs.observation_id
                )
            ):
                return finish(
                    "SOURCE_UNAVAILABLE",
                    limitations=["Governed source metadata could not be validated."],
                )
        if not self._same_release(result.release_id):
            return finish(
                "SOURCE_UNAVAILABLE",
                limitations=["The governed release changed during the answer."],
            )

        claims: list[Claim] = []
        cited: list[EvidenceReference] = []
        limitations: list[str] = []
        freshness: list[Freshness] = []
        by_id = {item.observation_id: item for item in metadata.metadata} if metadata else {}
        for obs in result.observations:
            freshness.append(
                Freshness(
                    observation_id=obs.observation_id,
                    source_timestamp=obs.source_published_at,
                )
            )
            limitations.extend(obs.limitations)
            if intent == "gap":
                continue
            if intent == "freshness":
                continue
            if intent == "comparison" and obs.value_state not in {
                ValueState.ZERO,
                ValueState.OBSERVED,
            }:
                return finish(
                    "INSUFFICIENT_EVIDENCE",
                    limitations=[
                        f"{obs.geography.geography_id} is {obs.value_state.value}; "
                        "numeric comparison is unavailable."
                    ],
                )
            if intent == "provenance":
                item = by_id[obs.observation_id]
                limitations.extend(item.source.limitations)
                if item.methodology:
                    limitations.extend(item.methodology.limitations)
                text = _render_observation(obs, item)
            else:
                text = _render_observation(obs)
            if intent == "observation" and obs.value_state not in {
                ValueState.ZERO,
                ValueState.OBSERVED,
            }:
                return finish(
                    "INSUFFICIENT_EVIDENCE",
                    limitations=[
                        f"The governed row is {obs.value_state.value}; "
                        "no numeric observation supports this answer."
                    ],
                )
            cited.append(obs.evidence)
            claims.append(
                Claim(
                    claim_id=f"atlas:observation:{obs.observation_id}",
                    text=text,
                    structured_refs=[obs.evidence],
                )
            )
        if intent == "gap":
            for row in result.coverage:
                if row.state == "absent":
                    claims.append(
                        Claim(
                            claim_id=f"atlas:coverage:{row.coverage_id}",
                            text=_render_coverage(row),
                            coverage_ids=[row.coverage_id],
                        )
                    )
            if not claims:
                # A positive coverage statement needs its exact bounded slot.
                claims = [
                    Claim(
                        claim_id=f"atlas:coverage:{row.coverage_id}",
                        text=_render_coverage(row),
                        coverage_ids=[row.coverage_id],
                    )
                    for row in result.coverage
                ]
        if intent == "comparison":
            if len(result.observations) != 2:
                return finish(
                    "INSUFFICIENT_EVIDENCE",
                    limitations=["Both requested county observations are required for comparison."],
                )
            first, second = result.observations
            try:
                comparison_text = _render_comparison(first, second)
            except ValueError:
                return finish(
                    "INSUFFICIENT_EVIDENCE",
                    limitations=["The governed observations are not comparable."],
                )
            claims.append(
                Claim(
                    claim_id="atlas:comparison",
                    text=comparison_text,
                    structured_refs=[first.evidence, second.evidence],
                )
            )
        if intent == "freshness":
            return finish(
                "INSUFFICIENT_EVIDENCE",
                limitations=[
                    "No governed source-specific freshness policy is available; "
                    "current or stale status cannot be established."
                ],
            )
        if not claims:
            return finish(
                "INSUFFICIENT_EVIDENCE",
                limitations=["No governed evidence supports the requested answer."],
            )
        limitations.append(
            "Freshness is unknown without a governed source-specific policy and timestamp."
        )
        return finish(
            "ANSWERED",
            claims=claims,
            refs=cited,
            coverage=result.coverage if intent == "gap" else [],
            freshness=freshness if cited else [],
            limitations=list(dict.fromkeys(limitations)),
            release=result.release_id,
        )

    def _call(self, tool: str, raw: dict[str, Any]) -> ToolResult:
        bounded = dict(raw)
        bounded["tool"] = tool
        # This explicit dispatch is the entire registered #18 tool inventory.
        if tool == "find_measures":
            method = self.tools.find_measures
        elif tool == "get_observations":
            method = self.tools.get_observations
        elif tool == "get_evidence_metadata":
            method = self.tools.get_evidence_metadata
        else:
            raise ValueError("unregistered tool")
        try:
            returned = method(bounded)
            # Pydantic trusts an existing model instance, including mutable nested
            # objects. Cross the tool boundary through a fresh wire representation.
            payload = (
                returned.model_dump(mode="json", warnings="error")
                if isinstance(returned, ToolResult)
                else returned
            )
            result = ToolResult.model_validate(payload)
        except Exception:
            return ToolResult(tool=tool, status="error", error_code="SOURCE_UNAVAILABLE")  # type: ignore[arg-type]
        return result

    def _same_release(self, release: str | None) -> bool:
        if not release:
            return False
        try:
            return self.tools.verify_release() == release
        except Exception:
            return False

    @staticmethod
    def _valid(result: ToolResult, tool: str, release: str | None = None) -> bool:
        if result.tool != tool or result.status != "ok" or not result.release_id:
            return False
        if release is not None and result.release_id != release:
            return False
        if result.error_code is not None:
            return False
        if tool == "find_measures":
            return (
                bool(result.measures)
                and not (result.observations or result.coverage or result.metadata)
                and all(m.release_version == result.release_id for m in result.measures)
            )
        if tool == "get_evidence_metadata":
            return (
                bool(result.metadata)
                and not (result.measures or result.observations or result.coverage)
                and all(
                    item.source.release_version == result.release_id
                    and (
                        item.methodology is None
                        or item.methodology.release_version == result.release_id
                    )
                    for item in result.metadata
                )
            )
        observations = {obs.observation_id: obs for obs in result.observations}
        if result.measures or result.metadata or not result.coverage:
            return False
        if len(observations) != len(result.observations):
            return False
        present = {row.observation_id for row in result.coverage if row.state == "present"}
        if present != set(observations):
            return False
        if len({row.coverage_id for row in result.coverage}) != len(result.coverage):
            return False
        for row in result.coverage:
            if (
                row.release_id != result.release_id
                or (row.state == "absent" and row.observation_id is not None)
                or (row.state == "present" and row.observation_id not in observations)
            ):
                return False
        return all(
            obs.release_id == result.release_id
            and obs.evidence.resource_type == "observation"
            and obs.evidence.resource_id == obs.observation_id
            and obs.evidence.release_id == result.release_id
            for obs in result.observations
        )

    @staticmethod
    def _valid_observation_scope(
        result: ToolResult, query: dict[str, Any], requested_years: set[int]
    ) -> bool:
        requested = {(geo, year) for geo in query["geography_ids"] for year in requested_years}
        rows = {(row.geography.geography_id, row.period_start.year): row for row in result.coverage}
        if len(rows) != len(result.coverage) or set(rows) != requested:
            return False
        observations = {obs.observation_id: obs for obs in result.observations}
        for slot, row in rows.items():
            year = slot[1]
            if (
                row.geography.geography_type.value != "county"
                or row.period_start != date(year, 1, 1)
                or row.period_end != date(year, 12, 31)
                or row.measure_id != query["measure_id"]
            ):
                return False
            if row.state == "present":
                obs = observations[row.observation_id or ""]
                if (
                    obs.geography != row.geography
                    or obs.period_start != row.period_start
                    or obs.period_end != row.period_end
                    or obs.measure_id != row.measure_id
                    or obs.evidence.source_id != obs.source_id
                    or obs.evidence.provenance_ref != obs.provenance_ref
                    or obs.evidence.methodology_version != obs.methodology_version
                    or obs.evidence.semantic_version != obs.semantic_version
                ):
                    return False
        return True
