"""Regression checks for the production governed-read API configuration."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_MANIFEST = REPO / ".do" / "app.yaml"


def test_production_api_manifest_targets_only_governed_read_database() -> None:
    manifest = APP_MANIFEST.read_text(encoding="utf-8")

    assert "key: SNOWFLAKE_ROLE\n        value: OH_LYME_PROD_READ" in manifest
    assert "key: SNOWFLAKE_DATABASE\n        value: ONE_HEALTH_LYME_GAP_ATLAS_PROD" in manifest
    assert (
        "key: SNOWFLAKE_PRESENTATION_DATABASE\n        value: ONE_HEALTH_LYME_GAP_ATLAS_PROD"
    ) in manifest
    assert "value: OH_LYME_API_READER" not in manifest
    assert "value: ONE_HEALTH_LYME_GAP_ATLAS\n" not in manifest
