"""Versioned, privacy-safe deterministic Ask Atlas experiment gates.

The caller runs the real bounded assistant against controlled service adapters.
Only case identifiers, versions, aggregate measurements and trace IDs leave the
request boundary. Evidence and answer text are inspected in memory only.
"""

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field

from .ask_atlas_mixed_composition import Composition
from .ask_atlas_orchestration import StructuredAssistantResponse


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    mode: Literal["Structured", "Both"]
    question: str
    context: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: str
    expected_sources: list[str]
    expected_tools: list[str] = Field(default_factory=list)
    expected_parameters: dict[str, Any] = Field(default_factory=dict)
    expected_value_state: str | None = None
    expected_value: float | None = None
    expected_cross_source_state: str | None = None
    expected_release: str | None = None
    expected_citation_ids: list[str] = Field(default_factory=list)


class EvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str = Field(pattern=r"^ask-atlas-eval-v[0-9]+$")
    cases: list[EvalCase] = Field(min_length=1)

    def validate_identity(self) -> None:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate eval case identity")


@dataclass(frozen=True)
class Candidate:
    name: str
    config_version: str
    code_commit: str
    provider: str
    model: str
    prompt_version: str
    tool_schema_version: str
    structured_release: str
    literature_corpus: str
    literature_index: str
    retrieval_version: str


@dataclass(frozen=True)
class Measurements:
    latency_ms: float | None = None
    tool_latency_ms: float | None = None
    dependency_latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_cost_usd: float | None = None


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    candidate: Candidate
    dataset_version: str
    passed: bool
    failures: tuple[str, ...]
    trace_id: str | None
    measurements: Measurements


def _trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


def evaluate(
    case: EvalCase,
    response: StructuredAssistantResponse | Composition,
    *,
    candidate: Candidate,
    dataset_version: str,
    measurements: Measurements | None = None,
) -> EvalResult:
    """Fail closed on objective claims; no LLM judge can override these gates."""
    failures: list[str] = []
    if isinstance(response, StructuredAssistantResponse):
        answer = response.answer
        outcome = answer.outcome
        sources = answer.actual_sources_used
        structured: StructuredAssistantResponse | None = response
        literature = None
        cross_state = None
    else:
        outcome = response.outcome
        sources = list(response.actual_sources_used)
        structured = response.structured
        literature = response.literature
        cross_state = response.cross_source_state
    if outcome != case.expected_outcome:
        failures.append("outcome")
    if sources != case.expected_sources:
        failures.append("source_routing")
    if cross_state != case.expected_cross_source_state:
        failures.append("cross_source_state")
    tools = [item.tool for item in structured.tool_evidence] if structured else []
    if tools != case.expected_tools:
        failures.append("tool_selection")
    if case.expected_tools and not structured:
        failures.append("structured_evidence")
    if structured:
        answer = structured.answer
        if case.expected_release and answer.replay.release_id != case.expected_release:
            failures.append("release_provenance")
        for tool in structured.tool_evidence:
            if tool.status != "ok" and outcome == "ANSWERED":
                failures.append("tool_failure_promoted")
        if case.expected_value_state is not None:
            observations = [
                obs for tool in structured.tool_evidence for obs in tool.observations
            ]
            if (
                len(observations) != 1
                or observations[0].value_state.value != case.expected_value_state
            ):
                failures.append("value_state")
            elif observations[0].value != case.expected_value:
                failures.append("value")
        if case.expected_parameters:
            observations = [
                obs for tool in structured.tool_evidence for obs in tool.observations
            ]
            for key, expected in case.expected_parameters.items():
                if not observations or getattr(observations[0], key, None) != expected:
                    failures.append("tool_parameters")
                    break
        for claim in answer.claims:
            if not (claim.structured_refs or claim.measure_ids or claim.coverage_ids):
                failures.append("unsupported_structured_claim")
            if any(word in claim.text.lower() for word in ("causes lyme", "proves causation")):
                failures.append("causal_promotion")
        if answer.claims and not answer.replay.release_id:
            failures.append("missing_release")
    if literature:
        citation_ids = [citation.citation_id for citation in literature.citations]
        if sorted(citation_ids) != sorted(case.expected_citation_ids):
            failures.append("citation_identity")
        for literature_claim in literature.claims:
            if (
                not literature_claim.citation_ids
                or not set(literature_claim.citation_ids) <= set(citation_ids)
            ):
                failures.append("unsupported_literature_claim")
            if any(
                word in literature_claim.text.lower()
                for word in ("causes lyme", "proves causation")
            ):
                failures.append("causal_promotion")
    elif case.expected_citation_ids:
        failures.append("missing_literature")
    if case.mode == "Both" and len(sources) == 2 and (
        outcome != "INSUFFICIENT_EVIDENCE" or cross_state != "insufficient_to_compare"
    ):
        failures.append("unverified_comparison")
    if (
        outcome != "ANSWERED"
        and not (case.mode == "Both" and len(sources) == 2)
        and structured
        and structured.answer.claims
    ):
        failures.append("failed_abstention")
    return EvalResult(
        case_id=case.case_id,
        candidate=candidate,
        dataset_version=dataset_version,
        passed=not failures,
        failures=tuple(sorted(set(failures))),
        trace_id=_trace_id(),
        measurements=measurements or Measurements(),
    )


def compare(
    dataset: EvalDataset, results: list[EvalResult], candidates: tuple[Candidate, Candidate]
) -> dict[str, Any]:
    """The same fixed cases must run for both configurations before promotion."""
    dataset.validate_identity()
    expected = {case.case_id for case in dataset.cases}
    summary: dict[str, Any] = {"dataset_version": dataset.dataset_version, "candidates": {}}
    for candidate in candidates:
        own = [item for item in results if item.candidate == candidate]
        if {item.case_id for item in own} != expected or len(own) != len(expected):
            raise ValueError("candidate did not run the complete fixed dataset")
        if any(item.dataset_version != dataset.dataset_version for item in own):
            raise ValueError("eval dataset version mismatch")
        summary["candidates"][candidate.name] = {
            "config_version": candidate.config_version,
            "passed": sum(item.passed for item in own),
            "failed": [item.case_id for item in own if not item.passed],
        }
    summary["promotion_gate"] = all(item.passed for item in results)
    return summary


def safe_case_key(case_id: str) -> str:
    """Stable correlation without exposing the question or evidence."""
    return sha256(case_id.encode("utf-8")).hexdigest()[:16]
