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
from .knowledge_chat import _unsafe_request

SourceMode = Literal["Literature", "Structured", "Both"]

_MIXED = re.compile(
    r"(?P<structured>.+?)\?\s+"
    r"(?P<literature>What does the governed literature say about "
    r"(?:Lyme disease|Lyme surveillance)\?)",
    re.IGNORECASE | re.DOTALL,
)
_ATLAS_SCOPE = re.compile(r"\b(?:Atlas|county\s+\d{5}|governed measure|observation)\b", re.I)
_STRUCTURED_NEED = re.compile(
    r"\b(?:case counts?|cases? (?:for|in|by)|counties|county|observations?|"
    r"governed measures?|Atlas|compare .+ counts?)\b", re.I,
)
_LITERATURE_FORM = re.compile(
    r"(?:What does the governed literature say about Lyme (?:disease|surveillance)|"
    r"What do published studies say about .+ Lyme disease|"
    r"What does the literature report about Lyme .+)\?", re.I,
)
_ARBITRARY_QUERY = re.compile(
    r"\b(?:select\s+.+\s+from|insert\s+into|delete\s+from|"
    r"update\s+.+\s+set|match\s*\(|cypher|sql|warehouse|"
    r"database credentials|repository)\b", re.I,
)
_PERSONAL_SUBJECT = re.compile(r"\b(?:i|my child)\b", re.I)
_TREATMENT_OBJECT = re.compile(r"\b(?:antibiotic\w*|medication\w*|treat(?:ment)?)\b", re.I)
_TREATMENT_ACTION = re.compile(r"\b(?:take|use|start|stop|receive|get)\b", re.I)
_ADVICE_REQUEST = re.compile(r"\b(?:should|which|what|need|can|could)\b", re.I)


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
    if (
        _unsafe_request(question)
        or _ARBITRARY_QUERY.search(question)
        or (
            _PERSONAL_SUBJECT.search(question)
            and _TREATMENT_OBJECT.search(question)
            and _TREATMENT_ACTION.search(question)
            and _ADVICE_REQUEST.search(question)
        )
    ):
        return SourceRoute(mode, None, None, refusal=True)
    if mode == "Structured":
        if _UNSAFE.search(question):
            return SourceRoute(mode, None, None, refusal=True)
        return SourceRoute(mode, question, None)
    if mode == "Literature":
        return SourceRoute(mode, None, question)

    match = _MIXED.fullmatch(question)
    if match is not None:
        structured = match.group("structured") + "?"
        if _UNSAFE.search(structured):
            return SourceRoute(mode, None, None, refusal=True)
        if _recognized_structured(structured):
            return SourceRoute(mode, structured, match.group("literature"))
        # Do not send an ambiguous Atlas observation to literature alone.
        return SourceRoute(mode, None, None)
    if _recognized_structured(question):
        if _UNSAFE.search(question):
            return SourceRoute(mode, None, None, refusal=True)
        return SourceRoute(mode, question, None)
    if _ATLAS_SCOPE.search(question) or _STRUCTURED_NEED.search(question):
        return SourceRoute(mode, None, None)
    if _LITERATURE_FORM.fullmatch(question):
        return SourceRoute(mode, None, question)
    return SourceRoute(mode, None, None)


def _recognized_structured(question: str) -> bool:
    intent = _intent(question)
    return intent is not None and match_question_form(question, intent) is not None
