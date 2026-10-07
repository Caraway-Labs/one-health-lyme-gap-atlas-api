"""Representative WEB460 mismatch; no production data or provenance invented."""

from datetime import date
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from test_api import FakePdfRenderer, FakeRepository
from test_environmental_context import fixture_row
from test_public_observations import row

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.environmental_context import FIELDS
from lyme_gap_atlas_api.reports.renderer import RendererFailure, RenderLimits
from lyme_gap_atlas_api.reports.renderers.typst import TypstRenderer


class WEB460Repository(FakeRepository):
    def load_snapshot(self):
        snapshot = super().load_snapshot()
        snapshot.metadata.release_id = "alpha-2026"
        snapshot.metadata.sources = []
        snapshot.metadata.limitations = "Sample"
        snapshot.counties[0] = snapshot.counties[0].model_copy(update={"release_id": "alpha-2026"})
        return snapshot


class WEB460Observations:
    release = "alpha-2026"
    missing = False
    caveat = "Surveillance sites do not represent the whole county."
    period = date(2023, 1, 1)

    def current_release(self):
        return self.release

    def measure_exists(self, measure_id, release):
        return measure_id == "tick_survey"

    def query(self, query, release, offset):
        values = list(row("08001", '"Reported"', "OBSERVED", "tick_survey"))
        values[4] = self.period
        values[13:18] = [release, "tick", "Tick survey", "2023", None]
        values[22:25] = [self.caveat, None, "Sample"]
        values[25:28] = [
            None if self.missing else "survey-source",
            "survey-dataset",
            "survey-method",
        ]
        return [tuple(values)]


class ContextPdfRenderer(FakePdfRenderer):
    def render(self, report, template_key):
        super().render(report, template_key)
        content = report.model_dump(mode="json")
        content["identity"].pop("generated_at")
        import json

        return b"%PDF-fixture\n" + json.dumps(content, sort_keys=True).encode()


def setup(renderer=None):
    observations = WEB460Observations()
    renderer = renderer or ContextPdfRenderer()
    api = TestClient(
        create_app(
            WEB460Repository(),
            ApiSettings(rate_limit_per_minute=1000),
            pdf_renderer=renderer,
            observation_repository=observations,
        )
    )
    return api, renderer, observations


PARAMS = {
    "template": "county-v2",
    "dataset_version": "alpha-2026",
    "period_start": "2023-01-01",
    "period_end": "2023-12-31",
    "measure_id": "tick_survey",
}
PATH = "/v1/counties/08001/report.pdf"


def test_web460_canonical_proof_direct_reload_and_legacy_isolation():
    api, renderer, observations = setup()
    visible = api.get(
        "/v1/observations",
        params={
            "measure_id": "tick_survey",
            "geography_type": "county",
            "geography_id": "08001",
            "year": 2023,
        },
    ).json()["data"]
    first = api.get(PATH, params=PARAMS)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    report = renderer.calls[-1][0]
    assert report.geography.identifier == "08001"
    assert report.provenance.dataset_version == "alpha-2026"
    assert report.provenance.sources == []
    assert report.observation_context.model_dump(mode="json")["observations"] == visible
    assert observations.caveat in visible[0]["limitations"]
    assert visible[0]["source_label"] == "Tick survey"
    assert visible[0]["source_url"] is None
    assert api.get(PATH, params=PARAMS).status_code == 200
    assert len(renderer.calls) == 1
    assert api.get(PATH).status_code == 200
    assert renderer.calls[-1][1] == "county-v1"
    assert api.get(PATH, params={"period_start": "2023-01-01"}).status_code == 422


@pytest.mark.parametrize("change", ["missing", "release", "period", "empty"])
def test_failed_or_stale_context_never_uses_cached_pdf(change):
    api, renderer, observations = setup()
    first = api.get(PATH, params=PARAMS)
    assert first.status_code == 200
    if change == "missing":
        observations.missing = True
    elif change == "release":
        observations.release = "new-release"
    elif change == "period":
        observations.period = date(2024, 1, 1)
    else:
        observations.query = lambda *args: []
    failed = api.get(PATH, params=PARAMS, headers={"If-None-Match": first.headers["etag"]})
    assert failed.status_code == 503
    assert failed.headers["cache-control"] == "no-store"
    assert len(renderer.calls) == 1


def test_caveat_change_rebuilds_and_failed_render_does_not_fallback():
    api, renderer, observations = setup()
    assert api.get(PATH, params=PARAMS).status_code == 200
    observations.caveat = "Updated caveat"
    renderer.error = RendererFailure("fixture failure")
    assert api.get(PATH, params=PARAMS).status_code == 503
    renderer.error = None
    assert api.get(PATH, params=PARAMS).status_code == 200
    assert len(renderer.calls) == 2
    assert "Updated caveat" in renderer.calls[-1][0].observation_context.observations[0].limitations


@pytest.mark.parametrize("key", ["dataset_version", "period_start", "period_end", "measure_id"])
def test_v2_requires_complete_explicit_context(key):
    api, renderer, _ = setup()
    assert api.get(PATH, params={k: v for k, v in PARAMS.items() if k != key}).status_code == 422
    assert renderer.calls == []


def test_real_typst_web460_text(real_typst_binary):
    limits = RenderLimits(
        timeout_seconds=5,
        max_pages=50,
        max_report_items=5000,
        max_individual_asset_bytes=5242880,
        max_aggregate_asset_bytes=20971520,
        max_pdf_bytes=26214400,
    )
    api, _, _ = setup(TypstRenderer(limits, binary=(real_typst_binary,)))
    response = api.get(PATH, params=PARAMS)
    assert response.status_code == 200, response.text
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    for value in (
        "08001",
        "alpha-2026",
        "2023-01-01",
        "2023-12-31",
        "Tick survey",
        "Surveillance sites do not represent the whole county.",
        "OBSERVED",
        "survey-source",
        "survey-dataset",
        "Unavailable",
        "Sample",
    ):
        assert value in text


@pytest.mark.parametrize(
    "updates, status",
    [
        ({"measure_id": "unpublished"}, 404),
        ({"measure_id": ["tick_survey", "tick_survey"]}, 422),
        ({"measure_id": "tick_survey,tick_survey"}, 422),
        ({"measure_id": ",".join(["tick_survey"] * 21)}, 422),
        ({"measure_id": ["tick_survey"] * 21}, 422),
        ({"period_end": "2022-12-31"}, 422),
        ({"period_end": "2025-12-31"}, 400),
    ],
)
def test_bounded_context_errors(updates, status):
    api, renderer, _ = setup()
    response = api.get(PATH, params={**PARAMS, **updates})
    assert response.status_code == status
    if status == 400:
        assert response.json()["code"] == "QUERY_TOO_BROAD"
        assert (
            "400"
            in api.app.openapi()["paths"]["/v1/counties/{fips}/report.pdf"]["get"]["responses"]
        )
    assert response.headers["cache-control"] == "no-store"
    assert renderer.calls == []


def test_changed_caveat_cannot_answer_old_etag_and_valid_context_can():
    api, renderer, observations = setup()
    first = api.get(PATH, params=PARAMS)
    assert (
        api.get(PATH, params=PARAMS, headers={"If-None-Match": first.headers["etag"]}).status_code
        == 304
    )
    observations.caveat = "New governed caveat"
    changed = api.get(PATH, params=PARAMS, headers={"If-None-Match": first.headers["etag"]})
    assert changed.status_code == 200
    assert changed.headers["etag"] != first.headers["etag"]
    assert len(renderer.calls) == 2


def test_orval_comma_array_and_repeated_selectors_have_identical_meaning():
    api, renderer, observations = setup()
    observations.measure_exists = lambda *args: True
    original = observations.query

    def query(filters, release, offset):
        values = list(original(filters, release, offset)[0])
        values[0] = "observation-" + filters.measure_id
        values[1] = filters.measure_id
        return [tuple(values)]

    observations.query = query
    csv = api.get(PATH, params={**PARAMS, "measure_id": "tick_survey,second_measure"})
    repeated = api.get(PATH, params={**PARAMS, "measure_id": ["second_measure", "tick_survey"]})
    assert csv.status_code == repeated.status_code == 200
    assert csv.content == repeated.content
    assert csv.headers["etag"] == repeated.headers["etag"]
    assert len(renderer.calls) == 1


class EnvironmentalReportRepository(FakeRepository):
    def load_snapshot(self):
        snapshot = super().load_snapshot()
        snapshot.metadata.release_id = "fixture-release"
        snapshot.counties[0] = snapshot.counties[0].model_copy(
            update={"release_id": "fixture-release"}
        )
        return snapshot


@pytest.mark.parametrize(
    "value, state, coverage",
    [
        (2.5, "OBSERVED", "COMPLETE"),
        (0.0, "ZERO", "COMPLETE"),
        (None, "MISSING", "PARTIAL_COVERAGE"),
        (None, "MISSING", "SOURCE_MISSING"),
        (None, "UNAVAILABLE", "OUT_OF_SOURCE_COVERAGE"),
    ],
)
def test_real_typst_environmental_context_pdf(real_typst_binary, value, state, coverage):
    class Observations:
        def current_release(self):
            return "fixture-release"

        def measure_exists(self, measure_id, release):
            return measure_id == "nclimgrid_prcp_county_day"

        def query(self, query, release, offset):
            values = dict(zip(FIELDS, fixture_row("08001", value, state, coverage), strict=True))
            values["source_time_present"] = coverage != "SOURCE_MISSING"
            # Exact DECIMAL support metadata and an honestly unavailable area.
            values["source_coverage_fraction"] = "0.75000"
            values["source_coverage_fraction_stored_type"] = "DECIMAL"
            values["source_coverage_fraction_native_double"] = None
            values["expected_area_m2"] = None
            values["expected_area_m2_stored_type"] = "NULL_VALUE"
            values["expected_area_m2_native_double"] = None
            return [tuple(values[field] for field in FIELDS)]

    renderer = TypstRenderer(RenderLimits.from_settings(ApiSettings()), binary=(real_typst_binary,))
    api = TestClient(
        create_app(
            EnvironmentalReportRepository(),
            ApiSettings(rate_limit_per_minute=1000),
            pdf_renderer=renderer,
            observation_repository=Observations(),
        )
    )
    visible = api.get(
        "/v1/observations",
        params={
            "measure_id": "nclimgrid_prcp_county_day",
            "geography_type": "county",
            "geography_id": "08001",
            "start_date": "2025-01-01",
            "end_date": "2025-01-01",
        },
    ).json()["data"][0]
    response = api.get(
        PATH,
        params={
            "template": "county-v2",
            "dataset_version": "fixture-release",
            "period_start": "2025-01-01",
            "period_end": "2025-01-01",
            "measure_id": "nclimgrid_prcp_county_day",
        },
    )
    assert response.status_code == 200, response.text
    text = " ".join(
        " ".join(page.extract_text().split()) for page in PdfReader(BytesIO(response.content)).pages
    )
    expected = [
        "08001",
        "fixture-release",
        "2025-01-01",
        "nclimgrid_prcp_county_day",
        "NOAA NCEI",
        "noaa_nclimgrid_daily",
        "nclimgrid-daily-v1.0.0-scaled",
        "atlas-nclimgrid-county-day/2",
        "fixture-metadata",
        "fixture-weight",
        "fixture-tiger-2025",
        "0.75000",
        "Source coverage fraction",
        "Valid fraction of supported area",
        "Upstream modification metadata",
        "Unavailable",
        "Source time present " + ("No" if coverage == "SOURCE_MISSING" else "Yes"),
        "Value state: " + state,
        "Coverage status " + coverage,
        "Value: " + ("Data unavailable" if value is None else "0" if value == 0 else str(value)),
        visible["provenance_ref"],
        visible["environmental_context"]["day_convention"],
        *visible["limitations"],
    ]
    for phrase in expected:
        assert " ".join(phrase.split()) in text
