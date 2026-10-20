"""Published County Data Brief copy.

The five workstreams, four questions, and county qualifications are a
deterministic port of the Atlas Explorer follow-up in web commit e6c0aa2:

- ``src/lib/follow-up-response-content.ts``
- ``src/lib/follow-up-workflows.ts`` (``buildCountyFollowUp`` with no review override)
- ``src/features/ux-reset/review/review-rationale.ts``

The PDF does not accept client-supplied recommendation text. A separate
state-review ``insufficient_evidence`` override is not an input of this
county report, so an empty Explorer review context is used.
"""

from datetime import UTC, datetime
from typing import Literal, TypeGuard, TypeVar

from ..models import CountyDetail
from .models import BriefLink, BriefRow, CountyDataBrief

COPY_VERSION = "explorer-follow-up-web-e6c0aa2-editorial-773"
FACTORS_INTRO = (
    "These factors summarize the published data used in the Atlas. "
    "They do not show how much each factor contributed to the county's overall score."
)
_NEUTRAL_COLOR = "#e4e9ea"
BRANDING = "ONE HEALTH LYME GAP ATLAS"
REPORT_TITLE = "COUNTY DATA BRIEF"
ATLAS_ORIGIN = "https://onehealthatlas.org"

FollowUpId = Literal[
    "clinical-laboratory-reporting",
    "community-context-outreach",
    "coordination-documentation",
    "human-disease-surveillance",
    "vector-pathogen-surveillance",
]
ResponseStatus = Literal["assigned", "unavailable"]
UnavailableReason = Literal[
    "gap-score-unavailable",
    "recommendation-unassigned",
    "unsupported-jurisdiction",
]
PathogenKind = Literal["missing", "negative", "other", "present"]

_RESPONSE_BY_COLOR = {
    "#a9d2db": "Continue routine review",
    "#55a8a3": "Monitor the pattern",
    "#87b982": "Verify with local information",
    "#efc64a": "Conduct targeted follow-up",
    "#f49a32": "Coordinate a local assessment",
    "#e9602b": "Prioritize targeted follow-up",
}
_CATEGORY_ORDER: tuple[FollowUpId, ...] = (
    "human-disease-surveillance",
    "vector-pathogen-surveillance",
    "clinical-laboratory-reporting",
    "community-context-outreach",
    "coordination-documentation",
)
_CATEGORY_LABELS: dict[FollowUpId, str] = {
    "clinical-laboratory-reporting": "Clinical and laboratory reporting",
    "community-context-outreach": "Community context and outreach",
    "coordination-documentation": "Coordination and documentation",
    "human-disease-surveillance": "Human disease surveillance",
    "vector-pathogen-surveillance": "Vector and pathogen surveillance",
}
_DOCUMENTED_TICK = frozenset({"established", "reported"})
_NEGATIVE_PATHOGEN = frozenset({"negative", "no detection", "not detected", "tested negative"})
_RUCC_LABELS = {
    1: "metro area of 1 million or more",
    2: "metro area of 250,000 to 1 million",
    3: "metro area of fewer than 250,000",
    4: "urban population of 20,000 or more, adjacent to a metro area",
    5: "urban population of 20,000 or more, not adjacent to a metro area",
    6: "urban population of 2,500 to 19,999, adjacent to a metro area",
    7: "urban population of 2,500 to 19,999, not adjacent to a metro area",
    8: "completely rural or less than 2,500 urban population, adjacent to a metro area",
    9: "completely rural or less than 2,500 urban population, not adjacent to a metro area",
}
_GAP_TITLE = "Gap score unavailable"
_GAP_EXPLANATION = (
    "Available published data do not support calculation of an Atlas gap score for this county."
)
_REVIEW_UNAVAILABLE = "Review level unavailable"
_VERIFICATION_NOTE = (
    "These topics are for checking published records. They are not a scored recommendation "
    "or an instruction to escalate surveillance."
)
_GOVERNANCE_QUESTIONS = (
    "Why is a follow-up category not assigned for this county?",
    "Which published evidence is missing, limited, or outside the supported scope?",
    "Are relevant state or local records available to clarify the evidence without "
    "assuming a particular response level?",
    "Should these limitations be documented for routine review rather than treated "
    "as an assigned priority?",
)
_RESPONSE_EXPLANATIONS = {
    "Conduct targeted follow-up": "Investigate a specific, evidence-supported uncertainty.",
    "Continue routine review": (
        "Use existing surveillance and program processes. Do not initiate an investigation "
        "solely because this county is scored."
    ),
    "Coordinate a local assessment": (
        "Bring together relevant public-health programs when important questions "
        "require information from multiple sources."
    ),
    "Monitor the pattern": (
        "Watch for meaningful changes and determine whether new evidence warrants verification."
    ),
    "Prioritize targeted follow-up": (
        "Consider giving greater attention to focused, locally governed review when "
        "the published response category is strongest."
    ),
    "Verify with local information": (
        "Reconcile published surveillance information with locally held evidence."
    ),
}
_RESPONSE_QUESTIONS: dict[str, tuple[str, str, str, str]] = {
    "Continue routine review": (
        "Are current surveillance processes sufficient to review this county as new data "
        "become available?",
        "Are gaps in the published human or vector evidence best documented for future review?",
        "Do available local records suggest a reason to depart from established "
        "monitoring practices?",
        "Are current prevention and communication activities appropriate for the community "
        "context already known?",
    ),
    "Monitor the pattern": (
        "Are changes in human surveillance findings consistent across comparable "
        "reporting periods?",
        "Are new tick or pathogen findings supported by collection dates and locations "
        "relevant to this county?",
        "Could changes in surveillance definitions, testing, or reporting practices explain "
        "an apparent change?",
        "What new evidence would justify moving beyond routine monitoring?",
    ),
    "Verify with local information": (
        "Do locally held case records agree with the published county count or minimum "
        "case-count floor?",
        "Could travel or likely exposure locations explain differences between residence-based "
        "cases and local vector evidence?",
        "Are newer or unpublished tick and pathogen surveillance records available?",
        "Does verification identify a substantive concern, or does the published pattern have "
        "a reasonable local explanation?",
    ),
    "Conduct targeted follow-up": (
        "Which specific inconsistency in the published or locally verified evidence warrants "
        "targeted follow-up?",
        "Could residence-versus-exposure differences, reporting practices, or data suppression "
        "account for the apparent discrepancy?",
        "Would reviewing existing vector records or obtaining targeted new information "
        "materially change a public-health decision?",
        "What finding would justify additional action, and what finding would support "
        "returning to routine review?",
    ),
    "Coordinate a local assessment": (
        "Which surveillance questions require information or expertise from more than one program?",
        "Do human case records and vector/pathogen evidence describe compatible time periods "
        "and geographic areas?",
        "Would coordinated review of existing records resolve the uncertainty before new "
        "surveillance or outreach is considered?",
        "Which findings would support a targeted response, and which would argue for "
        "continued observation?",
    ),
    "Prioritize targeted follow-up": (
        "What verified evidence makes this county a stronger candidate for focused follow-up "
        "than other current program priorities?",
        "Have alternative explanations—including travel-associated exposure, surveillance "
        "limitations, and reporting practices—been considered?",
        "What specific action could reduce important uncertainty or improve a locally "
        "supported public-health response?",
        "What additional evidence, operational capacity, and local approval would be needed "
        "before proceeding?",
    ),
}
_GAP_QUESTIONS = (
    "Why is a published Atlas gap score or follow-up category unavailable for this county?",
    "Which published evidence is missing, limited, or outside the supported scope?",
    "Are relevant state or local records available to clarify the evidence without "
    "assuming a particular response level?",
    "Should these limitations be documented for routine review rather than treated "
    "as an assigned priority?",
)
_RESPONSE_ROWS: dict[str, dict[FollowUpId, str]] = {
    "Continue routine review": {
        "clinical-laboratory-reporting": (
            "Maintain awareness of existing reporting and laboratory surveillance procedures. "
            "Consider whether routine reporting updates warrant review."
        ),
        "community-context-outreach": (
            "Continue existing prevention education and outreach activities as appropriate."
        ),
        "coordination-documentation": (
            "Document the published data, any new information, and key limitations during "
            "routine program review. No additional investigation is required based on the "
            "current score."
        ),
        "human-disease-surveillance": (
            "Continue routine review of Lyme disease surveillance data through established "
            "program processes."
        ),
        "vector-pathogen-surveillance": (
            "Review new tick and pathogen information as part of established program activities."
        ),
    },
    "Monitor the pattern": {
        "clinical-laboratory-reporting": (
            "Monitor whether changes in laboratory reporting, testing volume, or "
            "case-ascertainment practices could affect surveillance patterns."
        ),
        "community-context-outreach": (
            "Review whether existing prevention messages and outreach coverage remain "
            "appropriate as surveillance information changes."
        ),
        "coordination-documentation": (
            "Record the findings or changes that would warrant closer review, and identify "
            "which routine surveillance sources should be revisited."
        ),
        "human-disease-surveillance": (
            "Watch for changes in published Lyme disease reports and locally available "
            "surveillance summaries."
        ),
        "vector-pathogen-surveillance": (
            "Track newly available tick presence and pathogen detection records, including "
            "collection dates and geographic coverage where known."
        ),
    },
    "Verify with local information": {
        "clinical-laboratory-reporting": (
            "Review existing clinical and laboratory reporting information when local case "
            "ascertainment remains unclear."
        ),
        "community-context-outreach": (
            "Use locally held information to interpret healthcare access, rurality, and social "
            "vulnerability before changing prevention outreach."
        ),
        "coordination-documentation": (
            "Identify unresolved questions and the state or local program partners who may "
            "hold relevant information."
        ),
        "human-disease-surveillance": (
            "Compare published county Lyme disease case information with state or local "
            "surveillance records."
        ),
        "vector-pathogen-surveillance": (
            "Consult local vector surveillance records to clarify tick presence, pathogen "
            "findings, and collection coverage."
        ),
    },
    "Conduct targeted follow-up": {
        "clinical-laboratory-reporting": (
            "Where local records identify a case-ascertainment question, conduct a focused "
            "review of relevant clinical or laboratory reporting pathways."
        ),
        "community-context-outreach": (
            "Assess whether a verified surveillance concern has a practical prevention or "
            "communication implication."
        ),
        "coordination-documentation": (
            "Define a focused question, identify the existing program or partner best "
            "positioned to address it, and document what information would resolve the "
            "uncertainty."
        ),
        "human-disease-surveillance": (
            "Identify the particular discrepancy or limitation in published human surveillance "
            "data that warrants closer review."
        ),
        "vector-pathogen-surveillance": (
            "Examine the specific tick or pathogen surveillance question raised by the "
            "available records."
        ),
    },
    "Coordinate a local assessment": {
        "clinical-laboratory-reporting": (
            "Where relevant, involve surveillance and laboratory-reporting partners to clarify "
            "reporting workflows and case-ascertainment practices."
        ),
        "community-context-outreach": (
            "Include public-health education or community partners when the local assessment "
            "identifies a communication or access concern."
        ),
        "coordination-documentation": (
            "Consider a coordinated review among relevant epidemiology, vector, laboratory, "
            "and prevention partners. Atlas does not assign tasks or deadlines."
        ),
        "human-disease-surveillance": (
            "Bring together available human disease surveillance information from appropriate "
            "state or local programs to examine the reported pattern."
        ),
        "vector-pathogen-surveillance": (
            "Coordinate with vector surveillance or entomology partners to assess tick "
            "distribution, pathogen findings, and sampling coverage."
        ),
    },
    "Prioritize targeted follow-up": {
        "clinical-laboratory-reporting": (
            "Where local findings indicate a meaningful case-ascertainment concern, consider "
            "focused discussion with relevant surveillance or laboratory-reporting partners."
        ),
        "community-context-outreach": (
            "Consider whether locally verified information supports focused prevention "
            "education or outreach. The review category alone does not justify a public warning."
        ),
        "coordination-documentation": (
            "Consider prioritizing a defined follow-up question using existing jurisdictional "
            "decision processes. Document supporting data, remaining uncertainties, and local "
            "criteria for proceeding or stepping back."
        ),
        "human-disease-surveillance": (
            "Consider prioritizing focused review of locally available Lyme disease "
            "surveillance information."
        ),
        "vector-pathogen-surveillance": (
            "Consider prioritizing review with vector surveillance partners of the relevant "
            "tick and pathogen findings."
        ),
    },
}
_GAP_ROWS: dict[FollowUpId, str] = {
    "clinical-laboratory-reporting": (
        "Review reporting information only if locally held surveillance records suggest a "
        "specific question."
    ),
    "community-context-outreach": (
        "Identify which community-context measures are available and which are missing."
    ),
    "coordination-documentation": (
        "Document why the gap score or follow-up category is unavailable. Do not assign "
        "priority, urgency, responsibility, or a deadline."
    ),
    "human-disease-surveillance": (
        "Identify what published human surveillance information is available and what is missing."
    ),
    "vector-pathogen-surveillance": (
        "Check whether tick and pathogen records exist outside the current published source."
    ),
}
_RESOURCES: tuple[tuple[str, str], ...] = (
    (
        "Lyme surveillance data",
        "https://ndc.services.cdc.gov/case-definitions/lyme-disease-2022/",
    ),
    (
        "Tick and pathogen data",
        "https://www.cdc.gov/ticks/data-research/facts-stats/tick-surveillance-data-sets.html",
    ),
    (
        "Clinical testing guidance",
        "https://www.cdc.gov/lyme/hcp/diagnosis-testing/index.html",
    ),
    (
        "Social vulnerability index",
        "https://www.atsdr.cdc.gov/place-health/php/svi/svi-frequently-asked-questions-faqs.html",
    ),
    (
        "Health insurance data",
        "https://www.census.gov/programs-surveys/sahie.html",
    ),
    (
        "Rural-urban classification",
        "https://www.ers.usda.gov/data-products/rural-urban-continuum-codes",
    ),
    (
        "County review methodology",
        f"{ATLAS_ORIGIN}/docs/releases-and-methodology",
    ),
)


class _Level:
    def __init__(
        self,
        *,
        title: str,
        explanation: str,
        status: ResponseStatus,
        reason: UnavailableReason | None,
    ) -> None:
        self.title = title
        self.explanation = explanation
        self.status = status
        self.reason = reason


def build_county_data_brief(detail: CountyDetail, generated_at: datetime) -> CountyDataBrief:
    """Build the published brief for one county. Ignores model-assisted priority."""

    level = _review_level(detail)
    copy_title = _copy_title(level)
    if copy_title is not None:
        level.explanation = _explanation_for(copy_title)
    rows = _workstreams(detail, level)
    questions = _questions_for(level)
    posture = level.explanation
    if level.status == "unavailable":
        posture = f"{posture} {_VERIFICATION_NOTE}"
    return CountyDataBrief(
        copy_version=COPY_VERSION,
        branding=BRANDING,
        report_title=REPORT_TITLE,
        county_heading=f"{detail.county}, {detail.state_name}",
        response_title=level.title,
        response_status=level.status,
        response_color=_response_color(detail, level),
        score_label=_displayed_score(detail, level),
        fips=detail.fips,
        why_it_surfaced=_why_it_surfaced(detail, level),
        factors_intro=FACTORS_INTRO,
        categories=_category_rows(detail),
        interpretation=_interpretation(detail),
        posture=posture,
        workstreams=rows,
        questions=questions,
        resources=_links(detail),
        traceability=_traceability(detail, generated_at),
    )


def _review_level(detail: CountyDetail) -> _Level:
    response = _RESPONSE_BY_COLOR.get(detail.color.strip().lower())
    if not detail.in_contiguous_tick_scope:
        return _Level(
            title=_REVIEW_UNAVAILABLE,
            explanation=(
                "This county is outside the contiguous tick scope of the current release, "
                "so a review level is not assigned."
            ),
            status="unavailable",
            reason="unsupported-jurisdiction",
        )
    if response:
        return _Level(
            title=response,
            explanation="",
            status="assigned",
            reason=None,
        )
    if detail.color.strip() == "":
        return _Level(
            title=_GAP_TITLE,
            explanation=_GAP_EXPLANATION,
            status="unavailable",
            reason="gap-score-unavailable",
        )
    return _Level(
        title=_REVIEW_UNAVAILABLE,
        explanation=(
            "The published score color is not an approved Action Center response, "
            "so a review level is not assigned."
        ),
        status="unavailable",
        reason="recommendation-unassigned",
    )


def _copy_title(level: _Level) -> str | None:
    if level.status == "assigned":
        return level.title
    if level.reason == "gap-score-unavailable":
        return _GAP_TITLE
    return None


def _explanation_for(title: str) -> str:
    if title == _GAP_TITLE:
        return _GAP_EXPLANATION
    return _RESPONSE_EXPLANATIONS[title]


def _rows_for(title: str) -> dict[FollowUpId, str]:
    if title == _GAP_TITLE:
        return _GAP_ROWS
    return _RESPONSE_ROWS[title]


def _questions_for(level: _Level) -> list[BriefRow]:
    title = _copy_title(level)
    if title == _GAP_TITLE:
        questions = _GAP_QUESTIONS
    elif title is None:
        questions = _GOVERNANCE_QUESTIONS
    else:
        questions = _RESPONSE_QUESTIONS[title]
    return [
        BriefRow(number=str(index), label="Question", text=text)
        for index, text in enumerate(questions, start=1)
    ]


def _workstreams(detail: CountyDetail, level: _Level) -> list[BriefRow]:
    recommendations = {
        "human-disease-surveillance": _human_recommendation(detail, level),
        "vector-pathogen-surveillance": _vector_recommendation(detail),
        "clinical-laboratory-reporting": _clinical_recommendation(detail),
        "community-context-outreach": _community_recommendation(detail),
        "coordination-documentation": _coordination_recommendation(level),
    }
    rows: list[BriefRow] = []
    for index, category_id in enumerate(_CATEGORY_ORDER, start=1):
        text = _with_category_copy(level, category_id, recommendations[category_id])
        rows.append(BriefRow(number=str(index), label=_CATEGORY_LABELS[category_id], text=text))
    return rows


_UNASSIGNED_LEADS: dict[FollowUpId, str] = {
    "clinical-laboratory-reporting": (
        "Maintain awareness of existing reporting and laboratory surveillance procedures."
    ),
    "community-context-outreach": (
        "Use available community-context measures only as context for access and outreach."
    ),
    "coordination-documentation": "",
    "human-disease-surveillance": (
        "Review the published human surveillance information that is available."
    ),
    "vector-pathogen-surveillance": (
        "Review the published tick and pathogen information that is available."
    ),
}


def _with_category_copy(level: _Level, category_id: FollowUpId, evidence: str) -> str:
    title = _copy_title(level)
    base = _UNASSIGNED_LEADS[category_id] if title is None else _rows_for(title)[category_id]
    if not base:
        return evidence
    if not evidence or evidence.startswith(base):
        return evidence if evidence else base
    return f"{base} {evidence}"


def _human_recommendation(detail: CountyDetail, _level: _Level) -> str:
    if _human_published(detail):
        if detail.case_count_floor_2023 == 0:
            return (
                "Compare the published observed zero with local surveillance records. An observed "
                "zero is not a missing record and does not establish that transmission is absent."
            )
        return (
            "Compare the published county case-count floor with state or local surveillance "
            "records, including reporting completeness and residence-versus-exposure differences "
            "where investigation notes already exist."
        )
    return (
        "Check whether additional county case information is available through local records, "
        "including suppressed or unallocated reports. A missing published count is not a finding "
        "of zero cases."
    )


def _vector_recommendation(detail: CountyDetail) -> str:
    tick_documented = _documented_tick(detail)
    tick_missing = _tick_record_missing(detail)
    pathogen = _pathogen_kind(detail)
    if tick_documented and pathogen == "present":
        return (
            "Review the published tick presence and Borrelia burgdorferi detection records, "
            "including the time and geographic coverage of collections where local records "
            "include that detail. Detection is not a human transmission estimate."
        )
    if tick_documented and pathogen == "negative":
        return (
            "A tick presence record is published, and the pathogen record is a documented "
            "negative finding rather than a missing record. Confirm the test methods and "
            "coverage with a vector-surveillance partner before treating that negative "
            "as countywide."
        )
    if tick_documented and pathogen == "missing":
        return (
            "A tick presence record is published, and no county-level Borrelia burgdorferi "
            "detection record is published. Determine whether pathogen testing exists outside "
            "this source. Missing records are not documented negative findings."
        )
    if tick_documented:
        return (
            f"The published Borrelia burgdorferi status is {detail.burgdorferi_status}. "
            "Review it with the tick record and local vector partners. This status is not "
            "treated as absence."
        )
    if tick_missing and pathogen == "present":
        return (
            "A Borrelia burgdorferi detection is published without a documented tick-presence "
            "classification in this record. Review both with a vector-surveillance partner. "
            "Do not infer countywide tick distribution from that detection alone."
        )
    if tick_missing and pathogen == "negative":
        return (
            "No published tick-presence classification is available, and the pathogen record "
            "is a documented negative finding. Confirm what was tested before treating either "
            "gap as countywide absence."
        )
    if tick_missing and pathogen == "other":
        return (
            "No documented tick-presence classification is published. The Borrelia burgdorferi "
            f"status is {detail.burgdorferi_status}, which is not treated as absence. Review "
            "both with a vector-surveillance partner."
        )
    if tick_missing:
        return (
            "Record gaps in published coverage without interpreting missing records as absence "
            "or initiating sampling solely because data are unavailable. Determine whether "
            "records exist outside the current source."
        )
    return (
        f"The published tick status is {detail.tick_status}. This is not a finding of tick "
        "absence. Check whether newer local records change how that status should be read."
    )


def _clinical_recommendation(detail: CountyDetail) -> str:
    if not _human_published(detail):
        return "This is not a finding that laboratories failed to report."
    if _pathogen_kind(detail) == "present":
        return (
            "Compare reporting pathways with the published human and pathogen records. "
            "This county record does not indicate a test for any individual patient."
        )
    return (
        "Check whether current reporting pathways match the human surveillance information "
        "already available locally. This county record does not show that reporting failed."
    )


def _community_recommendation(detail: CountyDetail) -> str:
    available = sum(
        (
            _finite(detail.svi_percentile),
            _finite(detail.uninsured_percent) or _finite(detail.uninsured_percentile),
            _finite(detail.rucc_2023),
        )
    )
    if available == 3:
        return (
            "Use social vulnerability, health insurance coverage, and rural-urban classification "
            "as context for access and outreach. These measures are not proof of transmission "
            "or underreporting."
        )
    if available == 0:
        return (
            "No published social vulnerability, insurance coverage, or rural–urban "
            "classification is available. Identify that local information gap before deciding "
            "whether outreach is appropriate."
        )
    return (
        "Community context is incomplete in the published record. Review the measures that "
        "are available, and the ones that are missing, before deciding whether outreach is "
        "appropriate. These measures are not proof of transmission or underreporting."
    )


def _coordination_recommendation(level: _Level) -> str:
    if _copy_title(level) is not None:
        return ""
    return (
        "Document which data are missing and identify possible sources for clarification. "
        "These notes support local verification. Atlas does not assign an owner, a deadline, "
        "or an escalation."
    )


def _category_rows(detail: CountyDetail) -> list[BriefRow]:
    items = (
        ("1", "Published Lyme case data", _human_sentence(detail)),
        ("2", "Tick presence", _tick_sentence(detail)),
        ("3", "Borrelia burgdorferi detection", _pathogen_sentence(detail)),
        ("4", "Social vulnerability", _svi_sentence(detail)),
        ("5", "Health insurance coverage", _insurance_sentence(detail)),
        ("6", "Rurality", _rural_sentence(detail)),
    )
    return [BriefRow(number=number, label=label, text=text) for number, label, text in items]


def _why_it_surfaced(detail: CountyDetail, level: _Level) -> str:
    gaps = _surveillance_gap_sentence(detail)
    if level.reason == "gap-score-unavailable":
        lead = f"{detail.county} does not have a published gap review score. {level.explanation}"
        closing = "This brief is not a measure of individual risk."
    elif level.status == "assigned":
        score = _score_label(detail.score.score)
        lead = (
            f"{detail.county} has a published gap review score of {score}, "
            f"placing it in the {level.title} category."
        )
        closing = "This score is not a measure of individual risk."
    else:
        score = _score_label(detail.score.score)
        lead = (
            f"{detail.county} has a published gap review score of {score}. {level.explanation}"
        )
        closing = "This score is not a measure of individual risk."
    sentences = [lead]
    if gaps:
        sentences.append(gaps)
        sentences.append("Local information may help interpret these gaps.")
    sentences.append(closing)
    return " ".join(sentences)


def _interpretation(detail: CountyDetail) -> str:
    notes = [
        "Lyme disease data are grouped by county of residence, not necessarily the location "
        "of exposure.",
        "These values describe published surveillance, not individual risk.",
    ]
    if not _human_published(detail):
        notes.append("A missing published case count is not an observed zero.")
    elif detail.case_count_floor_2023 == 0:
        notes.append("The published human record is an observed zero, not a missing record.")
    if _tick_record_missing(detail):
        notes.append("A missing tick record does not establish that ticks are absent.")
    if _pathogen_kind(detail) == "missing":
        notes.append("Missing pathogen data is not a documented negative finding.")
    elif _pathogen_kind(detail) == "negative":
        notes.append(
            "A documented negative pathogen finding is distinct from a missing record "
            "and is not, by itself, a countywide absence finding."
        )
    notes.append("The percentage uninsured is context; the score uses the uninsured percentile.")
    return " ".join(notes)


def _human_sentence(detail: CountyDetail) -> str:
    floor = detail.case_count_floor_2023
    rate = detail.incidence_floor_2023
    unallocated = _unallocated_sentence(detail)
    if detail.human_status == "published_count_floor" and _finite(rate) and _finite(floor):
        rate_text = _one_decimal(rate)
        if floor == 0:
            sentence = (
                "The published 2023 county record is an observed zero, not a missing record. "
                f"The minimum rate supported by that floor was {rate_text} cases per 100,000. "
                "An observed zero does not establish that transmission is absent."
            )
        else:
            sentence = (
                f"The published 2023 county case-count floor is {_count(floor)}. "
                f"The minimum rate supported by that floor was {rate_text} cases per 100,000. "
                "That floor is not a complete case count or incidence estimate."
            )
    else:
        sentence = (
            "No county-level 2023 Lyme disease case count was published. "
            "A missing record is not treated as zero cases."
        )
    if unallocated is None:
        return sentence
    return f"{sentence} {unallocated}"


def _tick_sentence(detail: CountyDetail) -> str:
    species = [
        phrase
        for phrase in (
            _species_phrase("Ixodes scapularis", detail.scapularis_status),
            _species_phrase("Ixodes pacificus", detail.pacificus_status),
        )
        if phrase is not None
    ]
    if _documented_tick(detail):
        species_note = f" {' '.join(species)}" if species else ""
        return (
            f"Published tick surveillance classifies this county as {detail.tick_status.lower()}."
            f"{species_note} A published status is not a measure of tick abundance."
        )
    if _tick_record_missing(detail):
        return (
            "The published tick table has no county record; this does not establish tick absence."
        )
    return (
        f"The published tick status is {detail.tick_status}. This is not a finding of tick absence."
    )


def _pathogen_sentence(detail: CountyDetail) -> str:
    status = _normalized(detail.burgdorferi_status)
    if status == "present":
        return (
            "Borrelia burgdorferi was detected in tested ticks in the published surveillance "
            "data. Detection is not a human infection or transmission estimate."
        )
    if status in {"", "no records", "unknown"}:
        return (
            "No county-level Borrelia burgdorferi detection record is available; "
            "this does not establish pathogen absence."
        )
    return (
        f"The published Borrelia burgdorferi status is {detail.burgdorferi_status}. "
        "This is not a finding of pathogen absence."
    )


def _svi_sentence(detail: CountyDetail) -> str:
    if not _finite(detail.svi_percentile):
        return (
            "No county-level 2022 social vulnerability percentile was published. "
            "No estimated value is shown."
        )
    shown = _percent_points(detail.svi_percentile)
    return (
        f"The 2022 social vulnerability percentile is {shown}. Higher values indicate "
        "greater vulnerability on the source index. This is context for access and outreach, "
        "not proof of underreporting."
    )


def _insurance_sentence(detail: CountyDetail) -> str:
    if _finite(detail.uninsured_percent):
        percent = _one_decimal(detail.uninsured_percent)
        return (
            f"The published percentage uninsured is {percent}%. This is context for "
            "healthcare access, not proof that cases are being missed."
        )
    if _finite(detail.uninsured_percentile):
        return (
            "The published uninsured percentile is "
            f"{_percent_points(detail.uninsured_percentile)}. "
            "The percentage uninsured was not returned, so no percent is shown."
        )
    return (
        "No county-level health insurance coverage value was published. "
        "No estimated value is shown."
    )


def _rural_sentence(detail: CountyDetail) -> str:
    if not _finite(detail.rucc_2023):
        return (
            "No 2023 rural-urban classification was published for this county. "
            "No estimated category is shown."
        )
    code = int(detail.rucc_2023)
    label = _RUCC_LABELS.get(code)
    named = (
        f"USDA Rural-Urban Continuum Code {code} (2023): {label}."
        if label
        else (
            f"USDA Rural-Urban Continuum Code {code} (2023) has no approved "
            "plain-language label in Atlas."
        )
    )
    return (
        f"{named} Code 1 is the most metropolitan and 9 is the most rural. This is context "
        "for follow-up planning, not a tick-habitat or underreporting measure."
    )


def _links(_detail: CountyDetail) -> list[BriefLink]:
    """Keep the brief link row to the curated official sources shown in the design."""

    return [BriefLink(label=label, url=url) for label, url in _RESOURCES]


def _traceability(detail: CountyDetail, generated_at: datetime) -> list[str]:
    vintages = (
        "; ".join(f"{source.label} ({source.vintage})" for source in detail.release.sources)
        or "No source vintage was published with this release."
    )
    moment = generated_at if generated_at.tzinfo else generated_at.replace(tzinfo=UTC)
    stamp = moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    level = _review_level(detail)
    return [
        f"County: {detail.county}, {detail.state_name}",
        f"County identifier (FIPS): {detail.fips}",
        f"Published gap review score: {_displayed_score(detail, level)}",
        f"Review category: {level.title}",
        f"Data release: {detail.release.release_id}",
        f"Data methodology: {detail.release.methodology_version}",
        f"Score methodology: {detail.scoring_methodology_version}; smooth k=50",
        "Template version: county data brief v3.0",
        "Observation periods: Lyme case records 2023; social vulnerability 2022; rurality 2023.",
        "Lyme case records: 2023",
        "Social vulnerability: 2022",
        "Rurality: 2023",
        f"Source vintages: {vintages}",
        f"Release date: {moment.astimezone(UTC).strftime('%Y-%m-%d')}",
        f"Generated: {stamp}",
        f"Website: {ATLAS_ORIGIN.replace('https://', '')}",
        "Published release only. What-if score settings are not applied.",
        f"Copy: {COPY_VERSION}",
    ]


def _response_color(detail: CountyDetail, level: _Level) -> str:
    color = detail.color.strip().lower()
    if level.status != "assigned" or color not in _RESPONSE_BY_COLOR:
        return _NEUTRAL_COLOR
    return color


def _displayed_score(detail: CountyDetail, level: _Level) -> str:
    if level.reason == "gap-score-unavailable":
        return "Unavailable"
    return _score_label(detail.score.score)


def _surveillance_gap_sentence(detail: CountyDetail) -> str | None:
    missing = []
    if not _human_published(detail):
        missing.append("Lyme disease case")
    if _tick_record_missing(detail):
        missing.append("tick")
    if _pathogen_kind(detail) == "missing":
        missing.append("pathogen")
    if not missing:
        return None
    if len(missing) == 3:
        return (
            "The county has no county-level published Lyme disease case, tick, or pathogen "
            "records in the current datasets."
        )
    if len(missing) == 1:
        return f"No county-level published {missing[0]} record is in the current datasets."
    return (
        "No county-level published "
        f"{missing[0]} or {missing[1]} records are in the current datasets."
    )


def _species_phrase(name: str, status: str | None) -> str | None:
    if status and _normalized(status) in _DOCUMENTED_TICK:
        return f"{name} is {status.lower()}."
    return None


def _unallocated_sentence(detail: CountyDetail) -> str | None:
    count = detail.state_unallocated_records_2023
    if not _finite(count) or count <= 0:
        return None
    noun = "record was" if count == 1 else "records were"
    return (
        f"{_count(count)} published 2023 human-surveillance {noun} not allocated to a county "
        "in this release. Check whether any belong in this county before interpreting the "
        "county figure."
    )


def _documented_tick(detail: CountyDetail) -> bool:
    return any(
        _normalized(value) in _DOCUMENTED_TICK
        for value in (detail.tick_status, detail.scapularis_status, detail.pacificus_status)
    )


def _tick_record_missing(detail: CountyDetail) -> bool:
    if _documented_tick(detail):
        return False
    return all(
        _normalized(value) in {"", "no records"}
        for value in (detail.tick_status, detail.scapularis_status, detail.pacificus_status)
    )


def _human_published(detail: CountyDetail) -> bool:
    return detail.human_status == "published_count_floor" and _finite(detail.case_count_floor_2023)


def _pathogen_kind(detail: CountyDetail) -> PathogenKind:
    status = _normalized(detail.burgdorferi_status)
    if status == "present":
        return "present"
    if status in _NEGATIVE_PATHOGEN:
        return "negative"
    if status in {"", "missing", "no records", "suppressed", "unavailable", "unknown"}:
        return "missing"
    return "other"


def _normalized(value: str | None) -> str:
    return value.strip().lower() if value else ""


_Number = TypeVar("_Number", int, float)


def _finite(value: _Number | None) -> TypeGuard[_Number]:
    return value is not None


def _count(value: float) -> str:
    return f"{int(value):,}"


def _one_decimal(value: float) -> str:
    return f"{value:,.1f}"


def _percent_points(value: float) -> str:
    shown = value * 100 if 0 <= value <= 1 else value
    return _one_decimal(shown)


def _score_label(value: float) -> str:
    return _one_decimal(value)
