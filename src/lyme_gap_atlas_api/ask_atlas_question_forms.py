"""Finite, fully consumed question forms for the Structured Ask Atlas boundary.

Every admitted word has a reviewed role. Adding a question class requires a new
form and tests; arbitrary text never becomes a qualifier on selected context.
"""

import re
from dataclasses import dataclass

CASE_COUNT_MEASURE_ID = "case_count_floor_2023"
CASE_COUNT_INDICATOR_ID = "human_disease_burden"
_COUNT = r"(?P<case_subject>(?:Lyme )?case counts?(?: floor)?)"
_COUNT_PLURAL = r"(?P<case_subject>(?:Lyme )?case counts)"


def _form(body: str) -> re.Pattern[str]:
    return re.compile(rf"{body}[?.]?", re.ASCII | re.IGNORECASE)


_FORMS: dict[str, tuple[re.Pattern[str], ...]] = {
    "discovery": (
        _form(r"Which governed measure has label (?P<year>\d{4}) Lyme case count floor"),
        _form(r"Find measure for (?P<year>\d{4}) Lyme case count floor"),
        _form(r"Which governed measure is the Lyme case count floor"),
    ),
    "observation": (
        _form(rf"What is the (?P<year>\d{{4}}) {_COUNT} for county (?P<geo>\d{{5}})"),
        _form(rf"What is the {_COUNT} for county (?P<geo>\d{{5}}) in (?P<year>\d{{4}})"),
        _form(
            r"How many (?P<case_subject>cases) (?:were reported )?for county "
            r"(?P<geo>\d{5}) (?:during|in) (?P<year>\d{4})"
        ),
        _form(
            rf"Give the (?P<year>\d{{4}}) {_COUNT_PLURAL} for counties "
            r"(?P<geo>\d{5}) and (?P<geo2>\d{5})"
        ),
        _form(
            rf"Show the county (?P<geo>\d{{5}}) {_COUNT_PLURAL} for "
            r"(?P<start>\d{4}) to (?P<end>\d{4})"
        ),
        _form(r"Is the (?P<required_state>MISSING) row for county (?P<geo>\d{5}) zero"),
    ),
    "comparison": (
        _form(
            r"Compare (?P<year>\d{4}) county (?P<geo>\d{5}) with county "
            r"(?P<geo2>\d{5}) for the same measure"
        ),
    ),
    "gap": (
        _form(
            r"Which of counties (?P<geo>\d{5}) and (?P<geo2>\d{5}) "
            r"lacks this (?P<year>\d{4}) observation"
        ),
        _form(r"Does county (?P<geo>\d{5}) have the governed (?P<year>\d{4}) row"),
    ),
    "provenance": (
        _form(
            r"Where did the cited (?P<year>\d{4}) observation for county "
            r"(?P<geo>\d{5}) come from"
        ),
        _form(r"What is the county value while the source is unavailable"),
    ),
    "freshness": (
        _form(
            r"Is county (?P<geo>\d{5}) current under the stated source "
            r"freshness policy"
        ),
    ),
}


@dataclass(frozen=True)
class QuestionForm:
    intent: str
    years: frozenset[int] | None
    geographies: frozenset[str]
    explicit_case_subject: bool
    required_state: str | None


def match_question_form(question: str, intent: str) -> QuestionForm | None:
    """Return a reviewed form only when it consumes the entire input."""
    for pattern in _FORMS.get(intent, ()):
        matched = pattern.fullmatch(question.strip())
        if matched is None:
            continue
        fields = matched.groupdict()
        if fields.get("year") is not None:
            years = frozenset({int(fields["year"])})
        elif fields.get("start") is not None and fields.get("end") is not None:
            first, last = int(fields["start"]), int(fields["end"])
            years = frozenset(range(first, last + 1)) if first <= last else frozenset()
        else:
            years = None
        return QuestionForm(
            intent=intent,
            years=years,
            geographies=frozenset(
                value for key in ("geo", "geo2") if (value := fields.get(key)) is not None
            ),
            explicit_case_subject=fields.get("case_subject") is not None,
            required_state=fields.get("required_state"),
        )
    return None


def selector_matches_question(question: str, selector: str) -> bool:
    """The governed discovery selector may not switch the named subject."""
    if selector == CASE_COUNT_INDICATOR_ID:
        return match_question_form(question, "discovery") is not None
    if re.fullmatch(r"[A-Za-z0-9_ ]+", selector, re.ASCII) is None:
        return False

    def tokens(value: str) -> set[str]:
        return {
            {"cases": "case", "counts": "count"}.get(token, token)
            for token in re.findall(r"[a-z]+|\d+", value.casefold(), re.ASCII)
        }

    return tokens(selector) <= tokens(question)
