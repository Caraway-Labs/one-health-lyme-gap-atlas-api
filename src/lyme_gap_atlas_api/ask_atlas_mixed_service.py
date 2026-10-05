"""Single bounded in-process entry point over #19 and #14 services."""

from typing import Any, Protocol

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field

from .ask_atlas_mixed_composition import Composition, SourceMode, compose_results
from .ask_atlas_mixed_routing import route_question
from .ask_atlas_orchestration import (
    StructuredAssistantRequest,
    StructuredAssistantResponse,
    StructuredContext,
)
from .knowledge_chat import KnowledgeChatService
from .models import KnowledgeChatRequest, KnowledgeChatResponse


class MixedAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    source_mode: SourceMode = "Both"
    context: StructuredContext = Field(default_factory=StructuredContext)


class StructuredPort(Protocol):
    def ask(self, request: StructuredAssistantRequest) -> StructuredAssistantResponse: ...


class LiteraturePort(Protocol):
    def chat(
        self,
        request: KnowledgeChatRequest,
        request_id: str,
        network_identifier: str,
    ) -> KnowledgeChatResponse: ...


def _budget_exhausted(mode: SourceMode) -> Composition:
    with trace.get_tracer(__name__).start_as_current_span(
        "atlas.ask_atlas.mixed.composition", record_exception=False,
        set_status_on_exception=False,
    ) as span:
        span.set_attribute("atlas.ask_atlas.outcome", "SOURCE_UNAVAILABLE")
        span.set_attribute("atlas.ask_atlas.operational_outcome", "budget_exhaustion")
        span.set_attribute("atlas.ask_atlas.source_count", 0)
        return Composition(
            requested_source_mode=mode, outcome="SOURCE_UNAVAILABLE",
            actual_sources_used=(), cross_source_state=None,
            structured=None, literature=None,
            limitations=("The request budget is exhausted; retry later.",),
        )


class MixedAssistant:
    def __init__(
        self,
        structured: StructuredPort | None,
        literature: LiteraturePort | None,
    ) -> None:
        self.structured = structured
        self.literature = literature

    def ask(
        self,
        question: str,
        mode: SourceMode,
        context: StructuredContext,
        request_id: str,
        network_identifier: str,
        completion: dict[str, Any] | None = None,
    ) -> Composition:
        route = route_question(question, mode)
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(
            "atlas.ask_atlas.mixed.route", record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("atlas.ask_atlas.source_mode", mode)
            span.set_attribute(
                "atlas.ask_atlas.structured_requested", route.structured_question is not None
            )
            span.set_attribute(
                "atlas.ask_atlas.literature_requested", route.literature_question is not None
            )
            if route.refusal or not route.sources_requested:
                span.set_attribute(
                    "atlas.ask_atlas.outcome",
                    "SAFETY_REFUSAL" if route.refusal else "NEEDS_CLARIFICATION",
                )
                return Composition(
                    requested_source_mode=mode,
                    outcome="SAFETY_REFUSAL" if route.refusal else "NEEDS_CLARIFICATION",
                    actual_sources_used=(),
                    cross_source_state=None,
                    structured=None,
                    literature=None,
                    limitations=("The requested evidence scope cannot be admitted safely.",),
                )

        structured_result = None
        if route.structured_question is not None and self.structured is not None:
            with tracer.start_as_current_span(
                "atlas.ask_atlas.mixed.structured", record_exception=False,
                set_status_on_exception=False,
            ):
                try:
                    structured_result = self.structured.ask(
                        StructuredAssistantRequest(
                            question=route.structured_question,
                            source_mode="Structured",
                            context=context,
                        )
                    )
                except Exception:
                    structured_result = None
        if structured_result is not None and structured_result.answer.outcome == "SAFETY_REFUSAL":
            return compose_results(
                mode, structured=structured_result,
                structured_requested=True,
            )

        literature_result = None
        if route.literature_question is not None and self.literature is not None:
            with tracer.start_as_current_span(
                "atlas.ask_atlas.mixed.literature", record_exception=False,
                set_status_on_exception=False,
            ):
                try:
                    request = KnowledgeChatRequest(message=route.literature_question)
                    if isinstance(self.literature, KnowledgeChatService):
                        literature_result = self.literature.chat(
                            request, request_id, network_identifier, completion
                        )
                    else:
                        literature_result = self.literature.chat(
                            request, request_id, network_identifier
                        )
                except Exception:
                    literature_result = None
        # A refused budget must not turn an incomplete Both request into an
        # apparently complete one-source answer. Discard any earlier branch.
        if literature_result is not None and literature_result.status == "capacity_limited":
            return _budget_exhausted(mode)
        if completion is not None and completion.get("outcome") in {
            "budget_failure", "budget_exhausted", "deadline_exhausted"
        }:
            return _budget_exhausted(mode)
        with tracer.start_as_current_span(
            "atlas.ask_atlas.mixed.grounding", record_exception=False,
            set_status_on_exception=False,
        ):
            return compose_results(
                mode,
                structured=structured_result,
                literature=literature_result,
                structured_requested=route.structured_question is not None,
                literature_requested=route.literature_question is not None,
            )
