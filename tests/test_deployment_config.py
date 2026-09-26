"""Regression checks for the production governed-read API configuration."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_MANIFEST = REPO / ".do" / "app.yaml"
DOCKERFILE = REPO / "Dockerfile"

_FEEDBACK_IDEMPOTENCY_GATE_MESSAGE = (
    "This check cannot be removed until a storage-level or distributed "
    "idempotency design replaces the process lock."
)


def test_production_api_manifest_targets_only_governed_read_database() -> None:
    manifest = APP_MANIFEST.read_text(encoding="utf-8")

    assert "key: SNOWFLAKE_ACCOUNT\n        value: TXB06009" in manifest
    assert "key: SNOWFLAKE_ROLE\n        value: OH_LYME_PROD_READ" in manifest
    assert "key: SNOWFLAKE_DATABASE\n        value: ONE_HEALTH_LYME_GAP_ATLAS_PROD" in manifest
    assert (
        "key: SNOWFLAKE_PRESENTATION_DATABASE\n        value: ONE_HEALTH_LYME_GAP_ATLAS_PROD"
    ) in manifest
    assert "value: OH_LYME_API_READER" not in manifest
    assert "value: ONE_HEALTH_LYME_GAP_ATLAS\n" not in manifest
    assert "value: VIFDGUW-BVB26657" not in manifest


def test_feedback_idempotency_requires_single_process_topology() -> None:
    """Feedback V1 uniqueness depends on one API process; fail closed otherwise."""

    manifest = APP_MANIFEST.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    instance_match = re.search(r"^\s*instance_count:\s*(\d+)\s*$", manifest, re.MULTILINE)
    assert instance_match is not None, _FEEDBACK_IDEMPOTENCY_GATE_MESSAGE
    assert instance_match.group(1) == "1", _FEEDBACK_IDEMPOTENCY_GATE_MESSAGE

    for key in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        env_match = re.search(
            rf"key:\s*{key}\s*\n\s*value:\s*[\"']?(\d+)[\"']?",
            manifest,
        )
        if env_match is not None:
            assert int(env_match.group(1)) <= 1, _FEEDBACK_IDEMPOTENCY_GATE_MESSAGE

    workers_match = re.search(r"--workers(?:[=,\s]+)(\d+)", dockerfile)
    if workers_match is not None:
        assert workers_match.group(1) == "1", _FEEDBACK_IDEMPOTENCY_GATE_MESSAGE
