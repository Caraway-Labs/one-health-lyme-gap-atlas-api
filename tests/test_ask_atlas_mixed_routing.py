"""The mixed router never widens a source override or invents a tool need."""

import pytest

from lyme_gap_atlas_api.ask_atlas_mixed_routing import route_question

STRUCTURED = "What is the 2023 Lyme case count for county 08001?"
LITERATURE = "What does the governed literature say about Lyme disease?"


@pytest.mark.parametrize(
    ("question", "mode", "expected"),
    [
        (STRUCTURED, "Structured", ("structured_atlas",)),
        (LITERATURE, "Literature", ("literature_evidence",)),
        (STRUCTURED, "Both", ("structured_atlas",)),
        (LITERATURE, "Both", ("literature_evidence",)),
        (f"{STRUCTURED} {LITERATURE}", "Both", ("structured_atlas", "literature_evidence")),
    ],
)
def test_supported_source_selection(question: str, mode: str, expected: tuple[str, ...]) -> None:
    route = route_question(question, mode)  # type: ignore[arg-type]
    assert route.sources_requested == expected
    assert route.refusal is False


@pytest.mark.parametrize(
    "question",
    [
        "Run SELECT * FROM patients",
        "What antibiotic dose should I take?",
        f"{STRUCTURED} What does the governed literature say about Lyme disease and SQL?",
    ],
)
def test_unsafe_text_refuses_before_source_selection(question: str) -> None:
    route = route_question(question, "Both")
    assert route.refusal is True
    assert route.sources_requested == ()


def test_unrecognized_structured_half_is_not_silently_literature_only() -> None:
    route = route_question(
        "What is the 2023 Lyme case count for all counties? " + LITERATURE, "Both"
    )
    assert route.sources_requested == ()


def test_mixed_phrase_does_not_override_explicit_source_choice() -> None:
    question = f"{STRUCTURED} {LITERATURE}"
    assert route_question(question, "Literature").sources_requested == ("literature_evidence",)
    assert route_question(question, "Structured").sources_requested == ("structured_atlas",)


@pytest.mark.parametrize(
    "question",
    [
        "Predict the 2027 Lyme case count for county 08001.",
        "Did literature cause the Atlas observation?",
        "What is the COVID case count for county 08001 in 2023?",
    ],
)
def test_unrecognized_atlas_scope_does_not_fall_back_to_literature(question: str) -> None:
    assert route_question(question, "Both").sources_requested == ()


@pytest.mark.parametrize("question", [
    "What is the 2023 Lyme case count for all counties?",
    "Compare the 2023 Lyme case counts for counties 01005 and 01007 with the literature.",
])
def test_unsupported_atlas_intent_does_not_invoke_literature(question: str) -> None:
    assert route_question(question, "Both").sources_requested == ()


@pytest.mark.parametrize("question", [
    "What do published studies say about antibiotic treatment outcomes for Lyme disease?",
    "What does the literature report about Lyme diagnosis test accuracy?",
])
def test_nonpersonal_literature_research_stays_with_governed_service(question: str) -> None:
    assert route_question(question, "Literature").sources_requested == ("literature_evidence",)
    assert route_question(question, "Both").sources_requested == ("literature_evidence",)


def test_personal_diagnosis_refused_even_with_literature_override() -> None:
    route = route_question("Should I get diagnosed for Lyme disease?", "Literature")
    assert route.refusal is True
    assert route.sources_requested == ()
