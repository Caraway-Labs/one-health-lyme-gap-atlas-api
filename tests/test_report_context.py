"""Representative WEB460 mismatch; no production data or provenance invented."""

import shutil
from datetime import date
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from test_api import FakePdfRenderer, FakeRepository
from test_public_observations import row

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
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


@pytest.mark.skipif(shutil.which("typst") is None, reason="Production Typst binary unavailable")
def test_real_typst_web460_text():
    limits = RenderLimits(
        timeout_seconds=5,
        max_pages=50,
        max_report_items=5000,
        max_individual_asset_bytes=5242880,
        max_aggregate_asset_bytes=20971520,
        max_pdf_bytes=26214400,
    )
    api, _, _ = setup(TypstRenderer(limits))
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
        ({"measure_id": ["tick_survey"] * 21}, 422),
        ({"period_end": "2022-12-31"}, 422),
        ({"period_end": "2025-12-31"}, 400),
    ],
)
def test_bounded_context_errors(updates, status):
    api, renderer, _ = setup()
    response = api.get(PATH, params={**PARAMS, **updates})
    assert response.status_code == status
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
