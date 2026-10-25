"""Cross-repository k50 contract, ranking, export and cache validation."""

import csv
import io
from pathlib import Path

from lyme_gap_atlas_shared.domain import ScoreSettings, score_color
from test_api import FakeRepository, client

from lyme_gap_atlas_api.repository import Snapshot
from lyme_gap_atlas_api.service import AtlasService


def test_http_method_identity_incidence_and_legacy_rejection() -> None:
    api = client()
    response = api.get("/v1/atlas/scores?smooth_k=50")
    assert response.status_code == 200
    body = response.json()
    assert body["scoring_methodology_version"] == "surveillance-gap-score-v1.1"
    assert body["settings"] == ScoreSettings().model_dump()
    assert body["methodology_version"] == "alpha-0.2.0"
    assert "incidence_floor_2023" in body["counties"][0]
    assert api.get("/v1/atlas/scores?low_incidence_breakpoint=10").status_code == 422
    assert api.get("/v1/counties/08001?low_incidence_breakpoint=25").status_code == 422
    detail = api.get("/v1/counties/08001?smooth_k=50").json()
    assert detail["score"] == body["counties"][0]["score"]
    assert (
        detail["release"]["score_defaults"]
        == api.get("/v1/atlas/metadata").json()["score_defaults"]
    )
    assert detail["scoring_methodology_version"] == body["scoring_methodology_version"]
    assert api.get("/v1/atlas/scores?smooth_k=0").status_code == 422


def test_snapshot_api_scores_ranks_colors_details_csv_and_cache() -> None:
    with (Path(__file__).parent / "fixtures/gap-score-v11-k50.csv").open() as f:
        rows = list(csv.DictReader(f))
    base = FakeRepository().load_snapshot()
    records = []
    expected = {}
    for row in rows:
        records.append(
            base.counties[0].model_copy(
                update={
                    "fips": row["fips"],
                    "county": row["county"],
                    "state": row["state"],
                    "in_contiguous_tick_scope": row["in_contiguous_tick_scope"] == "True",
                    "human_status": row["human_status"],
                    "incidence_floor_2023": float(row["incidence_floor_2023"])
                    if row["incidence_floor_2023"]
                    else None,
                    "tick_status": row["tick_status"],
                    "burgdorferi_status": row["burgdorferi_status"],
                    "svi_percentile": float(row["svi_percentile"])
                    if row["svi_percentile"]
                    else None,
                    "uninsured_percentile": float(row["uninsured_percentile"])
                    if row["uninsured_percentile"]
                    else None,
                    "rucc_2023": int(row["rucc_2023"]) if row["rucc_2023"] else None,
                }
            )
        )
        if row["score"]:
            expected[row["fips"]] = row

    class Repository(FakeRepository):
        calls = 0

        def load_snapshot(self) -> Snapshot:
            self.calls += 1
            return Snapshot(metadata=base.metadata, counties=records)

    repository = Repository()
    service = AtlasService(repository)
    result = service.scores(ScoreSettings())
    eligible = [c for c in result.counties if c.in_contiguous_tick_scope]
    for rank, item in enumerate(eligible, 1):
        row = expected[item.fips]
        assert item.score.score == float(row["score"])
        assert rank == int(row["national_rank"])
        assert item.color == score_color(item.score.score)
        assert service.county(item.fips, ScoreSettings()).score == item.score
    exported = list(
        csv.DictReader(io.StringIO(service.ranking_csv(ScoreSettings(), "NY", "", "all")))
    )
    for item in exported:
        assert float(item["score"]) == float(expected[item["fips"]]["score"])
        assert item["smooth_k"] == "50"
        assert item["scoring_methodology_version"] == "surveillance-gap-score-v1.1"
    assert service.scores(ScoreSettings(smooth_k=100)) != result
    assert repository.calls == 1  # cached raw snapshot, never cached scoring settings


def test_default_pdf_brief_names_score_and_data_methods_separately() -> None:
    from lyme_gap_atlas_api.reports.service import ReportService

    report = ReportService(AtlasService(FakeRepository())).county_report(
        "08001", ScoreSettings(), template_version="county-v3"
    )
    assert report.brief is not None
    traceability = " ".join(report.brief.traceability)
    assert "surveillance-gap-score-v1.1; smooth k=50" in traceability
    assert "Data methodology: alpha-0.2.0" in traceability
