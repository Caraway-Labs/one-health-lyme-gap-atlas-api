"""Request-local composition of the existing Structured and Literature boundaries.

This module never generates a cross-source finding. Until a governed comparator
can verify all material scopes, two answered branches are insufficient to
compare. Original branch claims and provenance stay in their own typed bundles.
"""

from dataclasses import dataclass
from typing import Literal

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .ask_atlas_orchestration import StructuredAssistantResponse
from .models import KnowledgeChatResponse

SourceMode = Literal["Literature", "Structured", "Both"]
Outcome = Literal[
    "ANSWERED",
    "INSUFFICIENT_EVIDENCE",
    "SOURCE_UNAVAILABLE",
    "SAFETY_REFUSAL",
    "NEEDS_CLARIFICATION",
    "UNSUPPORTED_REQUEST",
    "QUERY_TOO_BROAD",
]


@dataclass(frozen=True)
class Composition:
    requested_source_mode: SourceMode
    outcome: Outcome
    actual_sources_used: tuple[Literal["structured_atlas", "literature_evidence"], ...]
    cross_source_state: Literal["insufficient_to_compare"] | None
    structured: StructuredAssistantResponse | None
    literature: KnowledgeChatResponse | None
    limitations: tuple[str, ...]


class MixedAssistantResponse(BaseModel):
    """Additive branch bundle; existing #17/#19 flat answers remain unchanged."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["ask-atlas-mixed-v1"] = "ask-atlas-mixed-v1"
    requested_source_mode: SourceMode
    outcome: Outcome
    actual_sources_used: list[Literal["structured_atlas", "literature_evidence"]]
    cross_source_state: Literal["insufficient_to_compare"] | None
    structured: StructuredAssistantResponse | None
    literature: KnowledgeChatResponse | None
    limitations: list[str]

    @model_validator(mode="after")
    def branch_identity(self) -> "MixedAssistantResponse":
        expected: list[Literal["structured_atlas", "literature_evidence"]] = []
        if self.structured and self.structured.answer.claims:
            expected.append("structured_atlas")
        if self.literature and self.literature.status == "answered":
            expected.append("literature_evidence")
        if self.actual_sources_used != expected:
            raise ValueError("source-used indicator does not match branch claims")
        if self.cross_source_state != (
            "insufficient_to_compare" if len(expected) == 2 else None
        ):
            raise ValueError("cross-source state requires two admitted classes")
        if len(expected) == 2 and self.outcome != "INSUFFICIENT_EVIDENCE":
            raise ValueError("unverified cross-source comparison cannot be answered")
        return self

    @classmethod
    def from_composition(cls, result: Composition) -> "MixedAssistantResponse":
        return cls(
            requested_source_mode=result.requested_source_mode,
            outcome=result.outcome,
            actual_sources_used=list(result.actual_sources_used),
            cross_source_state=result.cross_source_state,
            structured=result.structured,
            literature=result.literature,
            limitations=list(result.limitations),
        )


def _validated_structured(
    value: StructuredAssistantResponse | None,
) -> StructuredAssistantResponse | None:
    if value is None:
        return None
    try:
        return StructuredAssistantResponse.model_validate(value.model_dump(mode="json"))
    except (ValueError, ValidationError):
        return None


def _validated_literature(value: KnowledgeChatResponse | None) -> KnowledgeChatResponse | None:
    if value is None:
        return None
    try:
        result = KnowledgeChatResponse.model_validate(value.model_dump(mode="json"))
    except (ValueError, ValidationError):
        return None
    if result.status != "answered":
        return result if not (result.claims or result.citations) else None
    ids = [claim.claim_id for claim in result.claims]
    citations = {citation.citation_id: citation for citation in result.citations}
    if len(ids) != len(set(ids)) or len(citations) != len(result.citations):
        return None
    for claim in result.claims:
        if not claim.citation_ids or len(claim.citation_ids) != len(set(claim.citation_ids)):
            return None
        if any(
            citation_id not in citations
            or claim.claim_id not in citations[citation_id].claim_ids
            for citation_id in claim.citation_ids
        ):
            return None
    for citation in result.citations:
        if not citation.passage_ids or len(citation.claim_ids) != len(set(citation.claim_ids)):
            return None
        if any(
            claim_id not in ids
            or citation.citation_id not in next(
                claim.citation_ids for claim in result.claims if claim.claim_id == claim_id
            )
            for claim_id in citation.claim_ids
        ):
            return None
    return result


def compose_results(
    mode: SourceMode,
    *,
    structured: StructuredAssistantResponse | None = None,
    literature: KnowledgeChatResponse | None = None,
    structured_requested: bool = False,
    literature_requested: bool = False,
) -> Composition:
    """Admit only revalidated branch results; failure stays local to its source."""
    with trace.get_tracer(__name__).start_as_current_span(
        "atlas.ask_atlas.mixed.composition", record_exception=False,
        set_status_on_exception=False,
    ) as span:
        structured = _validated_structured(structured) if structured_requested else None
        literature = _validated_literature(literature) if literature_requested else None
        limitations: list[str] = []
        sources: list[Literal["structured_atlas", "literature_evidence"]] = []
        if structured_requested and structured is None:
            limitations.append("Structured Atlas evidence is unavailable for this request.")
        if literature_requested and literature is None:
            limitations.append("Governed literature evidence is unavailable for this request.")
        if structured is not None and structured.answer.outcome == "ANSWERED":
            sources.append("structured_atlas")
            limitations.extend(structured.answer.limitations)
        elif structured is not None:
            limitations.extend(structured.answer.limitations)
        if literature is not None and literature.status == "answered":
            sources.append("literature_evidence")
        if literature is not None and literature.status == "no_evidence":
            limitations.append(
                "No relevant admitted literature was found; this is not evidence of absence."
            )
        if literature is not None and literature.status in {
            "evidence_unavailable", "capacity_limited"
        }:
            limitations.append("Governed literature evidence is unavailable for this request.")
        if literature is not None and literature.status == "safety_refusal":
            # A source-level safety refusal cannot be converted into a mixed answer.
            outcome: Outcome = "SAFETY_REFUSAL"
            sources = []
            structured = None
            literature = None
        elif len(sources) == 2:
            outcome = "INSUFFICIENT_EVIDENCE"
            limitations.append(
                "Atlas and literature findings have no verified common geography, period, "
                "species, population, outcome, and relationship basis for comparison."
            )
        elif sources:
            outcome = "ANSWERED"
        elif structured is not None and structured.answer.outcome in {
            "NEEDS_CLARIFICATION", "UNSUPPORTED_REQUEST", "QUERY_TOO_BROAD", "SAFETY_REFUSAL"
        }:
            outcome = structured.answer.outcome
        elif (structured_requested and structured is None) or (
            literature_requested and literature is None
        ):
            outcome = "SOURCE_UNAVAILABLE"
        else:
            outcome = "INSUFFICIENT_EVIDENCE"
        state: Literal["insufficient_to_compare"] | None = (
            "insufficient_to_compare" if len(sources) == 2 else None
        )
        span.set_attribute("atlas.ask_atlas.outcome", outcome)
        span.set_attribute("atlas.ask_atlas.source_count", len(sources))
        span.set_attribute("atlas.ask_atlas.cross_source_state", state or "not_applicable")
        return Composition(
            requested_source_mode=mode,
            outcome=outcome,
            actual_sources_used=tuple(sources),
            cross_source_state=state,
            structured=structured,
            literature=literature,
            limitations=tuple(dict.fromkeys(limitations)),
        )
