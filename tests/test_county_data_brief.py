"""County Data Brief copy stays aligned with the Explorer follow-up port."""

from datetime import UTC, datetime

from lyme_gap_atlas_shared.domain import Score

from lyme_gap_atlas_api.app import _atlas_brief_filename
from lyme_gap_atlas_api.models import AtlasMetadata, CountyDetail, SourceMetadata
from lyme_gap_atlas_api.reports.county_data_brief import build_county_data_brief
from lyme_gap_atlas_api.reports.models import CountyReport, ReportGeography, ReportIdentity

_COLORS = {
    "#a9d2db": "Continue routine review",
    "#55a8a3": "Monitor the pattern",
    "#87b982": "Verify with local information",
    "#efc64a": "Conduct targeted follow-up",
    "#f49a32": "Coordinate a local assessment",
    "#e9602b": "Prioritize targeted follow-up",
}


def _detail(**overrides: object) -> CountyDetail:
    release = AtlasMetadata(
        release_id="governed-fixture",
        schema_version="2.0.0",
        generated_at=datetime(2026, 10, 15, tzinfo=UTC),
        loaded_at=datetime(2026, 10, 15, tzinfo=UTC),
        scope="US_COUNTIES",
        bundle_sha256="a" * 64,
        score_defaults={},
        methodology_version="semantic-2.0.0",
        limitations="Not a diagnosis or individual risk.",
        sources=[
            SourceMetadata(
                key="cdc",
                label="CDC",
                vintage="2023",
                url="https://www.cdc.gov/lyme/",
                note="Published surveillance",
            )
        ],
        states=[],
    )
    score = Score(
        score=7.6,
        human_weakness=75,
        ecological=0,
        community=28.9,
        tick_signal=0,
        pathogen_signal=0,
        svi_signal=2.3,
        access_signal=25.6,
        rural_signal=100,
    )
    values: dict[str, object] = {
        "fips": "19195",
        "county": "Worth County",
        "state": "IA",
        "state_name": "Iowa",
        "in_contiguous_tick_scope": True,
        "human_status": "no_county_linked_record",
        "tick_status": "No records",
        "burgdorferi_status": "No records",
        "evidence_completeness": 50,
        "score": score,
        "priority": "Lower Atlas priority — Not a safety finding",
        "color": "#a9d2db",
        "population": 7417,
        "case_count_floor_2023": None,
        "incidence_floor_2023": None,
        "state_unallocated_records_2023": 77,
        "scapularis_status": "No records",
        "pacificus_status": "No records",
        "svi_percentile": 0.0232,
        "uninsured_percentile": 0.2564,
        "uninsured_percent": 5.9,
        "rucc_2023": 9,
        "release": release,
    }
    values.update(overrides)
    return CountyDetail.model_validate(values)


def _brief(**overrides: object):
    return build_county_data_brief(_detail(**overrides), datetime(2026, 10, 18, 18, 0, tzinfo=UTC))


def test_worth_routine_brief_uses_published_gaps_not_prototype_notes() -> None:
    brief = _brief()

    assert brief.branding == "ONE HEALTH LYME GAP ATLAS"
    assert brief.report_title == "COUNTY DATA BRIEF"
    assert brief.response_title == "Continue routine review"
    assert brief.score_label == "7.6"
    assert [row.label for row in brief.categories] == [
        "Published Lyme case data",
        "Tick presence",
        "Borrelia burgdorferi detection",
        "Social vulnerability",
        "Health insurance coverage",
        "Rurality",
    ]
    human = brief.categories[0].text
    assert "missing record is not treated as zero" in human
    assert "77 published 2023 human-surveillance records were not allocated" in human
    assert "2.3" in brief.categories[3].text
    assert "5.9%" in brief.categories[4].text
    assert "Continuum Code 9" in brief.categories[5].text
    assert "not a measure of tick abundance" not in brief.categories[1].text
    assert "does not establish tick absence" in brief.categories[1].text
    assert "does not establish pathogen absence" in brief.categories[2].text
    assert len(brief.workstreams) == 5
    assert brief.workstreams[0].label == "Human disease surveillance"
    assert "missing published count is not a finding of zero cases" in brief.workstreams[0].text
    assert brief.questions[0].text.startswith("Are current surveillance processes")
    assert "Design prototype" not in brief.why_it_surfaced
    assert "What the data show" not in brief.why_it_surfaced
    assert any(item.url.startswith("https://") for item in brief.resources)


def test_response_questions_differ_for_every_published_color() -> None:
    questions = {
        title: tuple(row.text for row in _brief(color=color, score=_score(index * 15)).questions)
        for index, (color, title) in enumerate(_COLORS.items(), start=1)
    }

    assert set(questions) == set(_COLORS.values())
    assert len(set(questions.values())) == 6


def test_observed_zero_is_not_described_as_missing() -> None:
    brief = _brief(
        human_status="published_count_floor",
        case_count_floor_2023=0,
        incidence_floor_2023=0,
        state_unallocated_records_2023=0,
        color="#efc64a",
    )

    assert "observed zero" in brief.categories[0].text
    assert "missing record is not treated as zero" not in brief.categories[0].text
    assert brief.response_title == "Conduct targeted follow-up"
    assert "targeted follow-up" in brief.questions[0].text


def test_unavailable_review_level_uses_neutral_questions() -> None:
    outside = _brief(in_contiguous_tick_scope=False, color="#e4e9ea")
    unscored = _brief(color="")

    assert outside.response_title == "Review level unavailable"
    assert outside.response_status == "unavailable"
    assert outside.questions[0].text.startswith("Why is a follow-up category not assigned")
    assert unscored.response_title == "Gap score unavailable"
    assert unscored.questions[0].text.startswith("Why is a published Atlas gap score")
    assert outside.questions[0].text != unscored.questions[0].text


def test_brief_filename_is_short_and_ascii() -> None:
    report = CountyReport.model_construct(
        identity=ReportIdentity.model_construct(
            report_version="v1",
            template_version="county-v3",
            generated_at=datetime(2026, 10, 18, tzinfo=UTC),
        ),
        geography=ReportGeography(
            level="county",
            identifier="19195",
            name="Worth County",
            state_code="IA",
            state_name="Iowa",
        ),
    )

    assert _atlas_brief_filename(report) == "Worth_IA_AtlasBrief"

    saint = report.model_copy(
        update={
            "geography": report.geography.model_copy(
                update={"name": "St. Louis County", "state_code": "mo"}
            )
        }
    )
    assert _atlas_brief_filename(saint) == "St_Louis_MO_AtlasBrief"


def _score(value: float) -> Score:
    return Score(
        score=value,
        human_weakness=1,
        ecological=1,
        community=1,
        tick_signal=1,
        pathogen_signal=1,
        svi_signal=1,
        access_signal=1,
        rural_signal=1,
    )
