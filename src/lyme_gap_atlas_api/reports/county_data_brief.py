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

COPY_VERSION = "explorer-follow-up-web-e6c0aa2"
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
_PUBLISHED_SOURCE = (
    "Based on published Atlas gap-score category. "
    "This is not a model-assisted surveillance priority."
)
_VERIFICATION_NOTE = (
    "These rows are evidence-verification considerations, not a scored recommendation "
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
        "Maintain established surveillance. Do not initiate an investigation solely "
        "because the county is scored."
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
            "Maintain awareness of existing Lyme disease reporting and laboratory surveillance "
            "procedures. Consider whether routine reporting updates warrant review; the Atlas "
            "does not establish that reporting is incomplete."
        ),
        "community-context-outreach": (
            "Continue existing prevention education and outreach activities as locally "
            "appropriate. Use social vulnerability, insurance coverage, and rurality as "
            "contextual information, not evidence of transmission or underreporting."
        ),
        "coordination-documentation": (
            "Document the published evidence, its principal limitations, and any information "
            "to revisit during routine program review. No additional coordination or "
            "escalation is implied by this response."
        ),
        "human-disease-surveillance": (
            "Continue routine review of Lyme disease surveillance data through established "
            "program processes. Note limitations in published county counts, including "
            "reporting period and residence-based assignment, without assuming additional "
            "case investigation is needed."
        ),
        "vector-pathogen-surveillance": (
            "Review new tick and pathogen surveillance information as part of established "
            "program activities. Record gaps in published coverage without interpreting "
            "missing records as absence or initiating sampling solely because data are unavailable."
        ),
    },
    "Monitor the pattern": {
        "clinical-laboratory-reporting": (
            "Monitor whether changes in laboratory reporting, testing volume, or "
            "case-ascertainment practices could affect surveillance patterns. Investigate a "
            "reporting pathway only if local information identifies a specific concern."
        ),
        "community-context-outreach": (
            "Review whether existing prevention messages and outreach coverage remain "
            "appropriate as surveillance information changes. Use local knowledge to identify "
            "communication needs rather than assuming outreach gaps from county-level indicators."
        ),
        "coordination-documentation": (
            "Record the findings or changes that would warrant closer review, and identify "
            "which routine surveillance sources should be revisited. Keep monitoring "
            "proportional to program capacity and established practice."
        ),
        "human-disease-surveillance": (
            "Watch for changes in published Lyme disease reports and locally available "
            "surveillance summaries. Compare similar reporting periods and account for "
            "case-definition and reporting-method changes before interpreting an apparent trend."
        ),
        "vector-pathogen-surveillance": (
            "Track newly available tick presence and pathogen detection records, including "
            "collection dates and geographic coverage where known. Consider whether emerging "
            "findings change the current interpretation of the county's evidence."
        ),
    },
    "Verify with local information": {
        "clinical-laboratory-reporting": (
            "Review existing clinical and laboratory reporting information when local case "
            "ascertainment remains unclear. Determine whether reporting practices or "
            "case-classification methods explain differences without assuming reporting failure."
        ),
        "community-context-outreach": (
            "Use locally held information to interpret healthcare access, rurality, and social "
            "vulnerability before deciding whether changes to prevention outreach are justified. "
            "County-level context alone does not establish an outreach deficiency."
        ),
        "coordination-documentation": (
            "Identify unresolved evidence questions and the state or local program partners "
            "who may hold relevant information. Record what has been verified and what remains "
            "uncertain before deciding whether further action is needed."
        ),
        "human-disease-surveillance": (
            "Compare published county Lyme disease case information with state or local "
            "surveillance records. Where available, examine residence, likely exposure location, "
            "unallocated reports, reporting completeness, and differences in reporting periods."
        ),
        "vector-pathogen-surveillance": (
            "Consult local vector surveillance records to clarify tick presence, pathogen "
            "findings, collection coverage, and whether newer information exists outside "
            "published datasets. Distinguish missing records from documented negative findings."
        ),
    },
    "Conduct targeted follow-up": {
        "clinical-laboratory-reporting": (
            "Where local evidence identifies a case-ascertainment question, conduct a focused "
            "review of relevant clinical or laboratory reporting pathways. Consider "
            "case-definition requirements and reporting practices without presuming "
            "reporting failure."
        ),
        "community-context-outreach": (
            "Assess whether the verified surveillance concern has a practical prevention or "
            "communication implication. Consider existing local education activities and "
            "community access needs before recommending targeted outreach."
        ),
        "coordination-documentation": (
            "Define a focused question, identify the existing program or partner best "
            "positioned to address it, and document what information would resolve the "
            "uncertainty. Local program staff determine whether, when, and how follow-up proceeds."
        ),
        "human-disease-surveillance": (
            "Identify the particular discrepancy or limitation in published human surveillance "
            "evidence that warrants closer review. Compare relevant local case records, "
            "classification practices, and available exposure information to determine whether "
            "the apparent gap has an identifiable explanation."
        ),
        "vector-pathogen-surveillance": (
            "Examine the specific tick or pathogen surveillance question raised by the "
            "available evidence. Review collection methods, timing, geographic coverage, and "
            "existing test results before considering whether targeted additional surveillance "
            "would meaningfully reduce uncertainty."
        ),
    },
    "Coordinate a local assessment": {
        "clinical-laboratory-reporting": (
            "Where relevant, involve surveillance and laboratory-reporting partners to clarify "
            "reporting workflows, testing information, and case-ascertainment practices. Keep "
            "surveillance case definitions distinct from clinical diagnosis and treatment guidance."
        ),
        "community-context-outreach": (
            "Include public-health education or community partners when the local assessment "
            "identifies a communication or access concern. Use community context to tailor "
            "possible responses, not to infer disease burden or assign blame for reporting gaps."
        ),
        "coordination-documentation": (
            "Consider a coordinated review among relevant epidemiology, vector surveillance, "
            "laboratory, and prevention partners. Agree locally on key questions, available "
            "evidence, and criteria for any subsequent action; Atlas does not assign tasks "
            "or deadlines."
        ),
        "human-disease-surveillance": (
            "Bring together available human disease surveillance information from appropriate "
            "state or local programs to examine the reported pattern. Review relevant case "
            "counts, case-classification differences, geographic assignment, and unresolved "
            "reporting limitations."
        ),
        "vector-pathogen-surveillance": (
            "Coordinate with vector surveillance or entomology partners to assess tick "
            "distribution, pathogen findings, sampling coverage, and available local records. "
            "Identify whether existing information is sufficient or whether focused "
            "surveillance would inform the assessment."
        ),
    },
    "Prioritize targeted follow-up": {
        "clinical-laboratory-reporting": (
            "Where local findings indicate a meaningful case-ascertainment or reporting "
            "concern, consider focused discussion with relevant surveillance, clinical, or "
            "laboratory-reporting partners. Any education or workflow review should respond "
            "to an identified need, not an assumption of reporting failure."
        ),
        "community-context-outreach": (
            "Consider whether locally verified evidence supports focused prevention education "
            "or outreach in particular communities or settings. Use local knowledge of "
            "communication needs and healthcare access; the Atlas response category alone "
            "does not justify a public warning."
        ),
        "coordination-documentation": (
            "Consider prioritizing a defined, coordinated follow-up question using existing "
            "jurisdictional decision processes. Document supporting evidence, remaining "
            "uncertainties, possible actions, and local criteria for proceeding or stepping back."
        ),
        "human-disease-surveillance": (
            "Consider prioritizing focused review of locally available Lyme disease "
            "surveillance information. Reconcile consequential published limitations, relevant "
            "case and exposure information, and applicable reporting practices to determine "
            "whether further public-health action is warranted."
        ),
        "vector-pathogen-surveillance": (
            "Consider prioritizing review with vector surveillance partners of relevant tick "
            "and pathogen findings, including species, collection coverage, timing, and "
            "methods. Determine whether existing data are sufficient or whether narrowly "
            "targeted surveillance would inform a concrete decision."
        ),
    },
}
_GAP_ROWS: dict[FollowUpId, str] = {
    "clinical-laboratory-reporting": (
        "Review reporting information only if locally held surveillance records suggest a "
        "specific question. An unavailable Atlas gap score does not establish a clinical "
        "or laboratory reporting problem."
    ),
    "community-context-outreach": (
        "Identify which community-context measures are available and which are missing. "
        "Do not infer a prevention or outreach need from an unavailable gap score."
    ),
    "coordination-documentation": (
        "Document why the gap score or follow-up category is unavailable and what "
        "information might clarify that state. Do not assign priority, urgency, "
        "responsibility, or a deadline."
    ),
    "human-disease-surveillance": (
        "Identify what published human surveillance information is available and what is "
        "missing. Consult state or local records where appropriate before interpreting an "
        "unavailable county count as zero cases."
    ),
    "vector-pathogen-surveillance": (
        "Check whether tick and pathogen records exist outside the current published "
        "source. Distinguish unavailable records from documented negative findings, and "
        "do not initiate field collection solely because a record is absent."
    ),
}
_RESOURCES: tuple[tuple[str, str], ...] = (
    (
        "Lyme surveillance case definition",
        "https://ndc.services.cdc.gov/case-definitions/lyme-disease-2022/",
    ),
    (
        "County case data and residence-versus-exposure limitations",
        "https://www.cdc.gov/ticks/data-research/facts-stats/geographic-distribution-of-tickborne-disease-cases.html",
    ),
    (
        "Tick and pathogen record definitions and limitations",
        "https://www.cdc.gov/ticks/data-research/facts-stats/tick-surveillance-data-sets.html",
    ),
    (
        "Clinical testing guidance",
        "https://www.cdc.gov/lyme/hcp/diagnosis-testing/index.html",
    ),
    (
        "CDC Lyme disease prevention",
        "https://www.cdc.gov/lyme/prevention/index.html",
    ),
    (
        "Releases and methodology",
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
        score_label=_score_label(detail.score.score),
        fips=detail.fips,
        why_it_surfaced=_why_it_surfaced(detail, level),
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
        BriefRow(number=f"{index:02d}", label="Question", text=text)
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
        rows.append(BriefRow(number=f"{index:02d}", label=_CATEGORY_LABELS[category_id], text=text))
    return rows


def _with_category_copy(level: _Level, category_id: FollowUpId, evidence: str) -> str:
    title = _copy_title(level)
    if title is None:
        return evidence
    base = _rows_for(title)[category_id]
    if evidence.startswith(base):
        return evidence
    return f"{base} {evidence}"


def _category_base(level: _Level, category_id: FollowUpId) -> str | None:
    title = _copy_title(level)
    if title is None:
        return None
    return _rows_for(title)[category_id]


def _human_recommendation(detail: CountyDetail, level: _Level) -> str:
    unallocated = _unallocated_sentence(detail)
    if _human_published(detail):
        observed_zero = detail.case_count_floor_2023 == 0
        evidence = (
            "Compare the published observed zero with local surveillance records. An observed "
            "zero is not a missing record and does not establish that transmission is absent."
            if observed_zero
            else (
                "Compare the published county case-count floor with state or local surveillance "
                "records, including reporting completeness and residence-versus-exposure "
                "differences where investigation notes already exist."
            )
        )
    else:
        evidence = (
            "Check whether additional county case information is available through local "
            "surveillance records, including suppressed or unallocated reports. A missing "
            "published count is not a finding of zero cases."
        )
    base = _category_base(level, "human-disease-surveillance")
    prefixed = f"{base} {evidence}" if base else evidence
    if unallocated is None:
        return prefixed
    return f"{prefixed} {unallocated}"


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
            "No published tick or matching pathogen record is available for this county. "
            "Determine whether records exist outside the current source. Do not treat the gap "
            "as absence, and do not start field collection only because the published record "
            "is missing."
        )
    return (
        f"The published tick status is {detail.tick_status}. This is not a finding of tick "
        "absence. Check whether newer local records change how that status should be read."
    )


def _clinical_recommendation(detail: CountyDetail) -> str:
    if not _human_published(detail):
        return (
            "Review local laboratory and clinical reporting pathways if available surveillance "
            "records suggest questions about case ascertainment. This is not a finding that "
            "laboratories failed to report."
        )
    if _pathogen_kind(detail) == "present":
        return (
            "Check whether clinical and laboratory reporting pathways can be compared with the "
            "published human and pathogen records. This county evidence does not indicate a "
            "test for any individual patient."
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
            "Social vulnerability, health insurance coverage, and rural–urban classification "
            "are available as context. Consider whether existing prevention education reaches "
            "the communities those measures describe before adding outreach. They are not "
            "evidence of transmission or underreporting."
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
        "appropriate. These measures are not evidence of transmission or underreporting."
    )


def _coordination_recommendation(level: _Level) -> str:
    if level.status == "unavailable":
        return (
            "Document which evidence is missing and identify possible sources for "
            "clarification. These notes support local verification. Atlas does not assign "
            "an owner, a deadline, or an escalation."
        )
    return (
        "Document the outstanding evidence questions and identify appropriate surveillance "
        "or vector-program partners for local verification. Atlas does not assign that work "
        "or a completion date."
    )


def _category_rows(detail: CountyDetail) -> list[BriefRow]:
    items = (
        ("01", "Published Lyme case data", _human_sentence(detail)),
        ("02", "Tick presence", _tick_sentence(detail)),
        ("03", "Borrelia burgdorferi detection", _pathogen_sentence(detail)),
        ("04", "Social vulnerability", _svi_sentence(detail)),
        ("05", "Health insurance coverage", _insurance_sentence(detail)),
        ("06", "Rurality", _rural_sentence(detail)),
    )
    return [BriefRow(number=number, label=label, text=text) for number, label, text in items]


def _why_it_surfaced(detail: CountyDetail, level: _Level) -> str:
    place = f"{detail.county}, {detail.state_name}"
    if level.status == "assigned":
        lead = (
            f"The Atlas displays a published response of {level.title} for {place}. "
            f"The published gap score is {_score_label(detail.score.score)}."
        )
    else:
        lead = f"{level.explanation} This brief is for {place}."
    caveat = (
        "The notes below describe the published county record. They do not show how much "
        "each field contributed to the gap score, and they are not a measure of individual risk."
    )
    return f"{lead} {_PUBLISHED_SOURCE} {caveat}"


def _interpretation(detail: CountyDetail) -> str:
    notes = [
        "Lyme cases are assigned by county of residence, not necessarily the location of exposure.",
        "These data describe published surveillance, not individual risk.",
        "These values describe the published record. They do not show how much each field "
        "changed this county’s gap score. The percentage uninsured is context; the score "
        "uses the uninsured percentile.",
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
            "No county-level 2023 Lyme case count was published. "
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


def _links(detail: CountyDetail) -> list[BriefLink]:
    links = [BriefLink(label=label, url=url) for label, url in _RESOURCES]
    scapularis_documented = _normalized(detail.scapularis_status) in _DOCUMENTED_TICK
    pacificus_documented = _normalized(detail.pacificus_status) in _DOCUMENTED_TICK
    if not pacificus_documented or scapularis_documented:
        links.append(
            BriefLink(
                label="Blacklegged tick surveillance guidance",
                url="https://www.cdc.gov/ticks/resources/TickSurveillance_Iscapularis-P.pdf",
            )
        )
    if not scapularis_documented or pacificus_documented:
        links.append(
            BriefLink(
                label="Western blacklegged tick surveillance guidance",
                url="https://www.cdc.gov/ticks/resources/TickSurveillance_Ipacificus-P.pdf",
            )
        )
    if _finite(detail.svi_percentile):
        links.append(
            BriefLink(
                label="SVI interpretation",
                url="https://www.atsdr.cdc.gov/place-health/php/svi/svi-frequently-asked-questions-faqs.html",
            )
        )
    seen: set[str] = set()
    unique: list[BriefLink] = []
    for link in links:
        if link.url in seen:
            continue
        seen.add(link.url)
        unique.append(link)
    for source in detail.release.sources:
        if not source.url.startswith("https://") or source.url in seen:
            continue
        seen.add(source.url)
        unique.append(BriefLink(label=source.label, url=source.url))
    return unique


def _traceability(detail: CountyDetail, generated_at: datetime) -> list[str]:
    vintages = (
        "; ".join(f"{source.label} ({source.vintage})" for source in detail.release.sources)
        or "No source vintage was published with this release."
    )
    moment = generated_at if generated_at.tzinfo else generated_at.replace(tzinfo=UTC)
    stamp = moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return [
        f"County FIPS {detail.fips}",
        f"Published gap score {_score_label(detail.score.score)}",
        f"Published response: {_review_level(detail).title}",
        f"Data release {detail.release.release_id}",
        f"Methodology {detail.release.methodology_version}",
        f"Template county-v3 · copy {COPY_VERSION}",
        "Observation periods: Lyme case records 2023; social vulnerability 2022; rurality 2023.",
        f"Source vintages: {vintages}",
        f"Generated {stamp}",
    ]


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
