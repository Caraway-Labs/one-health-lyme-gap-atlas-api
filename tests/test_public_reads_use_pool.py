"""Public Snowflake reads borrow the shared read pool instead of logging in per call."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.environmental_context import EnvironmentalRepository
from lyme_gap_atlas_api.public_metadata import SnowflakeMetadataRepository
from lyme_gap_atlas_api.public_observations import SnowflakeObservationRepository
from lyme_gap_atlas_api.public_provenance import SnowflakeProvenanceRepository
from lyme_gap_atlas_api.snowflake_read_session import get_read_session_pool

SOURCE = Path(__file__).resolve().parents[1] / "src" / "lyme_gap_atlas_api"

# Modules that may open their own (non-pooled) connection, and why.
ALLOWED_DIRECT_CONNECT = {
    "dependency_telemetry.py": "wraps connect() with telemetry",
    "snowflake_connection.py": "defines connect()",
    "snowflake_read_session.py": "the pool is the one place a pooled connection is created",
    "snowflake_monitor.py": "dedicated dependency-monitor connection",
    "cdc_reader_probe.py": "readiness probe on an isolated connection",
    "county_context_probe.py": "readiness probe on an isolated connection",
    "environmental_reader_probe.py": "readiness probe on an isolated connection",
    "model_serving_probe.py": "readiness probe on an isolated connection",
    "snowflake_write_session.py": "the one place a pooled write connection is created",
    "feedback.py": "passes connect to the separate write pool (story #325)",
    "knowledge_chat.py": "passes connect to the separate write pool (#125, #325)",
    "cdc_surveillance.py": "alters session state; not in #318 scope (follow-up)",
    "repository.py": "ready() is unused by readiness since #310; load_snapshot is pooled",
}


def _imports_connect(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "connect" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_public_read_modules_do_not_import_connect_directly() -> None:
    offenders = [
        path.name
        for path in sorted(SOURCE.glob("*.py"))
        if path.name not in ALLOWED_DIRECT_CONNECT
        and _imports_connect(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], f"use read_connection() instead of connect() in: {offenders}"


def test_allowed_direct_connect_list_has_no_stale_entries() -> None:
    stale = [name for name in ALLOWED_DIRECT_CONNECT if not (SOURCE / name).exists()]
    assert stale == []


class _Cursor:
    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, *_: Any, **__: Any) -> None:
        return None

    def fetchone(self) -> tuple[str]:
        return ("release-1",)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []


class _Connection:
    def is_closed(self) -> bool:
        return False

    def close(self) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor()


@pytest.fixture
def logins(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    get_read_session_pool().close_all()
    created: list[int] = []

    def connect(_settings: Any) -> _Connection:
        created.append(1)
        return _Connection()

    monkeypatch.setattr("lyme_gap_atlas_api.snowflake_read_session.connect", connect)
    yield created
    get_read_session_pool().close_all()


def test_repeated_public_reads_share_one_login(logins: list[int]) -> None:
    settings = ApiSettings()
    provenance = SnowflakeProvenanceRepository(settings)
    metadata = SnowflakeMetadataRepository(settings)
    observations = SnowflakeObservationRepository(settings)
    environmental = EnvironmentalRepository(ApiSettings(environmental_context_enabled=True))

    for _ in range(3):
        provenance.current_release()
        metadata.load_metadata()
        with observations._cursor() as cursor:
            cursor.execute("SELECT 1")
        environmental.metadata()

    # Before this change each call above opened its own login (12 logins here).
    assert len(logins) == 1
