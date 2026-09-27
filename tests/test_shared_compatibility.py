"""Pin the installed portable surface and the API's explicit runtime extras."""

import json
import tomllib
from importlib import metadata
from pathlib import Path

from lyme_gap_atlas_shared.domain import (
    CountyInputs,
    Provenance,
    Score,
    ScoreSettings,
    normalize_county_fips,
    priority_label,
    score_color,
    score_county,
)
from lyme_gap_atlas_shared.observability import configure_logging, configure_tracing
from lyme_gap_atlas_shared.settings import SnowflakeSettings
from lyme_gap_atlas_shared.snowflake import connect

from lyme_gap_atlas_api.models import CountyDetail

SHARED_SHA = "83ccbe76047185f6aacaff551afd8b03d88aa5cf"
ROOT = Path(__file__).resolve().parents[1]


def test_installed_shared_version_source_and_explicit_extras() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    requirements = project["project"]["dependencies"]
    shared_requirement = next(
        item for item in requirements if item.startswith("one-health-lyme-gap-atlas-shared[")
    )
    shared_lock = next(
        item for item in lock["package"] if item["name"] == "one-health-lyme-gap-atlas-shared"
    )
    installed = metadata.distribution("one-health-lyme-gap-atlas-shared")
    source = json.loads(installed.read_text("direct_url.json") or "{}")

    assert shared_requirement.startswith(
        "one-health-lyme-gap-atlas-shared[snowflake,observability] @ "
    )
    assert shared_requirement.endswith(f"@{SHARED_SHA}")
    assert shared_lock["version"] == installed.version == "1.0.0"
    assert shared_lock["source"]["git"].endswith(f"#{SHARED_SHA}")
    assert source["vcs_info"]["commit_id"] == SHARED_SHA
    assert source["vcs_info"]["requested_revision"] == SHARED_SHA
    assert set(shared_lock["optional-dependencies"]) >= {"snowflake", "observability"}
    assert "legacy" not in shared_requirement
    assert callable(connect) and callable(configure_logging) and callable(configure_tracing)
    assert SnowflakeSettings is not None


def test_portable_exports_and_api_owned_public_dto() -> None:
    inputs = CountyInputs(
        fips=normalize_county_fips("08001"),
        in_contiguous_tick_scope=True,
        human_status="no_county_linked_record",
        incidence_floor_2023=None,
        tick_status="Established",
        burgdorferi_status="Present",
    )
    score = score_county(inputs, ScoreSettings())

    assert isinstance(score, Score)
    assert Score.model_validate_json(score.model_dump_json()) == score
    assert priority_label(score.score)
    assert score_color(score.score).startswith("#")
    assert "retrieved_at" in Provenance.model_fields
    assert CountyDetail.__module__ == "lyme_gap_atlas_api.models"
