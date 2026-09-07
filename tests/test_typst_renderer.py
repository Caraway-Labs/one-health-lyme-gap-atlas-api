import shutil
import sys
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest
from lyme_gap_atlas_shared import ScoreSettings
from pypdf import PdfReader
from test_reports import FakeRepository

from lyme_gap_atlas_api.reports.models import CountyReport, StateReport
from lyme_gap_atlas_api.reports.renderer import (
    RenderCompilationError,
    RenderLimits,
    RenderTimeout,
    ResourceLimitExceeded,
    UnknownTemplateError,
    validate_asset_sizes,
)
from lyme_gap_atlas_api.reports.renderers.typst import TypstRenderer
from lyme_gap_atlas_api.reports.service import ReportService
from lyme_gap_atlas_api.service import AtlasService


def _limits(**overrides: int | float) -> RenderLimits:
    return RenderLimits(
        timeout_seconds=float(overrides.get("timeout_seconds", 3)),
        max_pages=int(overrides.get("max_pages", 50)),
        max_report_items=int(overrides.get("max_report_items", 5_000)),
        max_individual_asset_bytes=int(
            overrides.get("max_individual_asset_bytes", 5 * 1024 * 1024)
        ),
        max_aggregate_asset_bytes=int(overrides.get("max_aggregate_asset_bytes", 20 * 1024 * 1024)),
        max_pdf_bytes=int(overrides.get("max_pdf_bytes", 25 * 1024 * 1024)),
    )


@pytest.fixture
def template_directory(tmp_path: Path) -> Path:
    path = tmp_path / "templates"
    path.mkdir()
    (path / "minimal.typ").write_text("= Fixture", encoding="utf-8")
    return path


@pytest.fixture
def fake_typst(tmp_path: Path) -> tuple[str, ...]:
    executable = tmp_path / "fake_typst.py"
    executable.write_text(
        """import os
import sys
import time
from pathlib import Path

from pypdf import PdfWriter

mode = os.environ.get('FAKE_TYPST_MODE', 'success')
if mode == 'timeout':
    time.sleep(1)
if mode == 'failure':
    sys.exit(1)
if mode == 'require_input' and not (Path(sys.argv[-2]).parent / 'input.json').is_file():
    sys.exit(1)
writer = PdfWriter()
for _ in range(int(os.environ.get('FAKE_TYPST_PAGES', '1'))):
    writer.add_blank_page(width=612, height=792)
with open(sys.argv[-1], 'wb') as output:
    writer.write(output)
""",
        encoding="utf-8",
    )
    return (sys.executable, str(executable))


@pytest.fixture
def report() -> CountyReport:
    return ReportService(AtlasService(FakeRepository())).county_report("08001", ScoreSettings())


def test_trusted_template_renders_a_valid_pdf(
    template_directory: Path, fake_typst: tuple[str, ...], report: CountyReport
) -> None:
    payload = TypstRenderer(
        _limits(), template_directory, {"minimal-v1": "minimal.typ"}, fake_typst
    ).render(report, "minimal-v1")

    assert payload.startswith(b"%PDF-")
    assert payload


def test_renderer_rejects_unknown_template(
    template_directory: Path, fake_typst: tuple[str, ...], report: CountyReport
) -> None:
    with pytest.raises(UnknownTemplateError):
        TypstRenderer(
            _limits(), template_directory, {"minimal-v1": "minimal.typ"}, fake_typst
        ).render(report, "../../untrusted")


def test_renderer_translates_timeout_and_compile_failure(
    monkeypatch: pytest.MonkeyPatch,
    template_directory: Path,
    fake_typst: tuple[str, ...],
    report: CountyReport,
) -> None:
    renderer = TypstRenderer(
        _limits(timeout_seconds=0.1), template_directory, {"minimal-v1": "minimal.typ"}, fake_typst
    )
    monkeypatch.setenv("FAKE_TYPST_MODE", "timeout")
    with pytest.raises(RenderTimeout):
        renderer.render(report, "minimal-v1")
    monkeypatch.setenv("FAKE_TYPST_MODE", "failure")
    with pytest.raises(RenderCompilationError):
        TypstRenderer(
            _limits(), template_directory, {"minimal-v1": "minimal.typ"}, fake_typst
        ).render(report, "minimal-v1")
    monkeypatch.delenv("FAKE_TYPST_MODE")


def test_renderer_enforces_output_page_and_input_limits(
    monkeypatch: pytest.MonkeyPatch,
    template_directory: Path,
    fake_typst: tuple[str, ...],
    report: CountyReport,
) -> None:
    renderer = TypstRenderer(
        _limits(max_pdf_bytes=10),
        template_directory,
        {"minimal-v1": "minimal.typ"},
        fake_typst,
    )
    with pytest.raises(ResourceLimitExceeded, match="size"):
        renderer.render(report, "minimal-v1")
    monkeypatch.setenv("FAKE_TYPST_PAGES", "2")
    with pytest.raises(ResourceLimitExceeded, match="page"):
        TypstRenderer(
            _limits(max_pages=1), template_directory, {"minimal-v1": "minimal.typ"}, fake_typst
        ).render(report, "minimal-v1")
    with pytest.raises(ResourceLimitExceeded, match="item"):
        TypstRenderer(
            _limits(max_report_items=1),
            template_directory,
            {"minimal-v1": "minimal.typ"},
            fake_typst,
        ).render(report, "minimal-v1")
    monkeypatch.delenv("FAKE_TYPST_PAGES")


def test_renderer_stages_input_next_to_nested_template(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_typst: tuple[str, ...],
    report: CountyReport,
) -> None:
    template_directory = tmp_path / "templates"
    template = template_directory / "county" / "v1" / "report.typ"
    template.parent.mkdir(parents=True)
    template.write_text("= Fixture", encoding="utf-8")
    monkeypatch.setenv("FAKE_TYPST_MODE", "require_input")

    payload = TypstRenderer(
        _limits(), template_directory, {"county-v1": "county/v1/report.typ"}, fake_typst
    ).render(report, "county-v1")

    assert payload.startswith(b"%PDF-")


def test_asset_limits_are_enforced() -> None:
    with pytest.raises(ResourceLimitExceeded, match="asset"):
        validate_asset_sizes([b"12"], _limits(max_individual_asset_bytes=1))
    with pytest.raises(ResourceLimitExceeded, match="aggregate"):
        validate_asset_sizes([b"12", b"34"], _limits(max_aggregate_asset_bytes=3))


@pytest.mark.skipif(shutil.which("typst") is None, reason="requires the pinned Typst binary")
@pytest.mark.parametrize(
    ("template_key", "report_factory", "required_text"),
    [
        (
            "county-v1",
            lambda service: service.county_report("08001", ScoreSettings()),
            (
                "Lyme Gap Atlas county report",
                "Adams",
                "Executive summary",
                "Data unavailable",
                "Methodology and provenance",
                "alpha-2026-08-06",
            ),
        ),
        (
            "state-v1",
            lambda service: service.state_report("CO", ScoreSettings()),
            (
                "Lyme Gap Atlas state report",
                "Colorado",
                "Executive summary",
                "Methodology and provenance",
                "alpha-2026-08-06",
            ),
        ),
    ],
)
def test_real_typst_templates_render_meaningful_report_content(
    template_key: str,
    report_factory: Callable[[ReportService], CountyReport | StateReport],
    required_text: tuple[str, ...],
) -> None:
    reports = ReportService(AtlasService(FakeRepository()))
    report = report_factory(reports)
    payload = TypstRenderer(_limits()).render(report, template_key)
    rendered_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(payload)).pages
    )

    for expected in required_text:
        assert expected in rendered_text
    assert "text(size:" not in rendered_text
