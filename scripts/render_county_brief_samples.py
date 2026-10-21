"""Render County Data Brief samples from live county payloads for WEB #773 UAT."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

from lyme_gap_atlas_shared.domain import ScoreSettings
from pypdf import PdfReader

from lyme_gap_atlas_api.app import _atlas_brief_filename
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.models import CountyDetail
from lyme_gap_atlas_api.reports.county_data_brief import build_county_data_brief
from lyme_gap_atlas_api.reports.models import (
    CountyReport,
    ReportGeography,
    ReportIdentity,
    ReportMetric,
    ReportProvenance,
    ReportScore,
    ReportSource,
)
from lyme_gap_atlas_api.reports.renderer import RenderLimits
from lyme_gap_atlas_api.reports.renderers.typst import TypstRenderer

API = "https://api.onehealthatlas.org"
SAMPLES = (
    ("06049", "modoc-ca"),
    ("19195", "worth-ia"),
    ("06037", "los-angeles-ca-higher-review"),
    ("02070", "dillingham-ak-out-of-scope"),
    ("09130", "lower-ct-river-valley-long-name"),
)


def _fetch(fips: str) -> CountyDetail:
    with urlopen(f"{API}/v1/counties/{fips}", timeout=60) as response:
        return CountyDetail.model_validate(json.loads(response.read().decode("utf-8")))


def _metric(key: str, label: str, value: int | float | str | None) -> ReportMetric:
    return ReportMetric(
        key=key,
        label=label,
        value=value,
        availability="available" if value is not None else "unavailable",
    )


def _report(detail: CountyDetail, generated_at: datetime) -> CountyReport:
    release = detail.release
    settings = ScoreSettings()
    return CountyReport(
        identity=ReportIdentity(
            report_version="v1",
            template_version="county-v3",
            generated_at=generated_at,
        ),
        geography=ReportGeography(
            level="county",
            identifier=detail.fips,
            name=detail.county,
            state_code=detail.state,
            state_name=detail.state_name,
        ),
        provenance=ReportProvenance(
            dataset_version=release.release_id,
            schema_version=release.schema_version,
            methodology_version=release.methodology_version,
            scoring_methodology_version=detail.scoring_methodology_version,
            scoring_settings=settings.model_dump(),
            limitations=release.limitations,
            sources=[
                ReportSource(
                    key=source.key,
                    label=source.label,
                    vintage=source.vintage,
                    url=source.url,
                    note=source.note,
                )
                for source in release.sources
            ],
        ),
        score=ReportScore(**detail.score.model_dump()),
        priority=detail.priority,
        population=_metric("population", "Population", detail.population),
        human_health=[],
        ecological=[],
        data_completeness=_metric(
            "evidence_completeness",
            "Evidence completeness",
            detail.evidence_completeness,
        ),
        brief=build_county_data_brief(detail, generated_at),
    )


def _download_before(fips: str, destination: Path) -> None:
    with urlopen(f"{API}/v1/counties/{fips}/report.pdf", timeout=120) as response:
        destination.write_bytes(response.read())
        content_disposition = response.headers.get("Content-Disposition", "")
        content_type = response.headers.get("Content-Type", "")
    destination.with_suffix(".headers.txt").write_text(
        f"content-type: {content_type}\ncontent-disposition: {content_disposition}\n",
        encoding="utf-8",
    )


def main() -> int:
    root = Path("artifacts/web-773-county-brief")
    before_dir = root / "before"
    after_dir = root / "after"
    before_dir.mkdir(parents=True, exist_ok=True)
    after_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC)
    renderer = TypstRenderer(
        RenderLimits.from_settings(ApiSettings()),
        binary=("typst",),
    )

    for fips, slug in SAMPLES:
        print(f"fetch {fips} ({slug})", flush=True)
        detail = _fetch(fips)
        (after_dir / f"{slug}.input.json").write_text(
            detail.model_dump_json(indent=2), encoding="utf-8"
        )
        report = _report(detail, generated_at)
        assert report.brief is not None
        filename = f"{_atlas_brief_filename(report)}.pdf"
        after_path = after_dir / f"{slug}__{filename}"
        payload = renderer.render(report, "county-v3")
        after_path.write_bytes(payload)
        reader = PdfReader(after_path)
        pages = len(reader.pages)
        assert pages == 2, f"{fips}: expected two pages, got {pages}"
        assert all(
            float(page.mediabox.width) == 612 and float(page.mediabox.height) == 792
            for page in reader.pages
        ), f"{fips}: expected US Letter pages"
        urls = {
            annotation.get_object()["/A"].get("/URI")
            for page in reader.pages
            for annotation in page.get("/Annots", [])
            if annotation.get_object().get("/A", {}).get("/S") == "/URI"
        }
        assert {link.url for link in report.brief.resources} <= urls
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        (after_dir / f"{slug}.summary.txt").write_text(
            "\n".join(
                [
                    f"fips={detail.fips}",
                    f"county={detail.county}, {detail.state_name}",
                    f"color={detail.color}",
                    f"score={detail.score.score}",
                    f"response={report.brief.response_title}",
                    f"pages={pages}",
                    f"filename={filename}",
                    f"release={detail.release.release_id}",
                    f"generated_at={generated_at.isoformat()}",
                    f"clickable_urls={len(urls)}",
                    f"score_label={report.brief.score_label}",
                    (
                        "has Recommended posture"
                        if "Recommended posture" in text
                        else "no Recommended posture"
                    ),
                    "has Follow-up areas" if "Follow-up areas" in text else "no Follow-up areas",
                    f"has_gap_review_score={'Gap review score' in text}",
                    f"has_fips_label={'County identifier (FIPS)' in text}",
                ]
            ),
            encoding="utf-8",
        )
        print(f"  after pages={pages} file={after_path.name}", flush=True)

        before_path = before_dir / f"{slug}__prod.pdf"
        _download_before(fips, before_path)
        print(
            f"  before pages={len(PdfReader(before_path).pages)} file={before_path.name}",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
