"""Conservative source routing for the unified Ask Atlas boundary.

Mixed routing only splits a finite, fully consumed question form. The
structured half is still validated by the existing #19 orchestrator; this
module never selects a measure, geography, tool, query, or literature passage.
"""

import re
from dataclasses import dataclass
from typing import Literal

from .ask_atlas_orchestration import _UNSAFE, _intent
from .ask_atlas_question_forms import match_question_form

SourceMode = Literal["Literature", "Structured", "Both"]

_MIXED = re.compile(
    r"(?P<structured>.+?)\?\s+"
    r"(?P<literature>What does the governed literature say about "
    r"(?:Lyme disease|Lyme surveillance)\?)",
    re.IGNORECASE | re.DOTALL,
)
_ATLAS_SCOPE = re.compile(r"\b(?:Atlas|county\s+\d{5}|governed measure|observation)\b", re.I)


@dataclass(frozen=True)
class SourceRoute:
    requested_source_mode: SourceMode
    structured_question: str | None
    literature_question: str | None
    refusal: bool = False

    @property
    def sources_requested(self) -> tuple[str, ...]:
        sources = []
        if self.structured_question is not None:
            sources.append("structured_atlas")
        if self.literature_question is not None:
            sources.append("literature_evidence")
        return tuple(sources)


def route_question(question: str, mode: SourceMode) -> SourceRoute:
    """An override never widens; Both uses only recognized source needs."""
    question = question.strip()
    if _UNSAFE.search(question):
        return SourceRoute(mode, None, None, refusal=True)
    if mode == "Structured":
        return SourceRoute(mode, question, None)
    if mode == "Literature":
        return SourceRoute(mode, None, question)

    match = _MIXED.fullmatch(question)
    if match is not None:
        structured = match.group("structured") + "?"
        if _recognized_structured(structured):
            return SourceRoute(mode, structured, match.group("literature"))
        # Do not send an ambiguous Atlas observation to literature alone.
        return SourceRoute(mode, None, None)
    if _recognized_structured(question):
        return SourceRoute(mode, question, None)
    if _ATLAS_SCOPE.search(question):
        return SourceRoute(mode, None, None)
    return SourceRoute(mode, None, question)


def _recognized_structured(question: str) -> bool:
    intent = _intent(question)
    return intent is not None and match_question_form(question, intent) is not None
