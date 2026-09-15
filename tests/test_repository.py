"""Repository tests for the governed semantic-release boundary."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.repository import (
    AtlasDataUnavailableError,
    SnowflakeAtlasRepository,
)


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self._result: list[tuple[Any, ...]] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)
        if "CURRENT_RELEASE_V" in statement:
            self._result = [
                (
                    "governed-2026-09-15",
                    "1.0.0",
                    "2026-09-15T00:00:00Z",
                    "2026-09-15T00:01:00Z",
                    "3144 counties",
                    "bundle-sha",
                    json.dumps({"ecological_share": 0.65}),
                    "semantic-1.0.0",
                    "Approved limitations",
                )
            ]
        elif "CURRENT_SOURCE_METADATA_V" in statement:
            self._result = [("human", "Human", "2023", "https://cdc.gov", "Floor")]
        else:
            self._result = [
                (
                    "governed-2026-09-15",
                    "08001",
                    "Adams",
                    "CO",
                    "Colorado",
                    100,
                    True,
                    "no_county_linked_record",
                    None,
                    None,
                    0,
                    "No records",
                    "No records",
                    "No records",
                    "No records",
                    0.5,
                    0.2,
                    10.0,
                    7,
                    50,
                    json.dumps({"type": "Polygon", "coordinates": []}),
                )
            ]

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._result

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self.cursor_value = cursor

    def cursor(self) -> _Cursor:
        return self.cursor_value

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_load_snapshot_uses_configured_governed_presentation_database(monkeypatch) -> None:
    cursor = _Cursor()
    connection = _Connection(cursor)
    monkeypatch.setattr("lyme_gap_atlas_api.repository.connect", lambda _settings: connection)
    settings = ApiSettings(
        snowflake_database="ONE_HEALTH_LYME_GAP_ATLAS",
        snowflake_presentation_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        snowflake_presentation_schema="PRESENTATION",
    )

    snapshot = SnowflakeAtlasRepository(settings).load_snapshot()

    assert snapshot.metadata.release_id == "governed-2026-09-15"
    assert snapshot.counties[0].fips == "08001"
    assert all(
        '"ONE_HEALTH_LYME_GAP_ATLAS_PROD"."PRESENTATION".' in sql for sql in cursor.statements
    )
    assert all("ONE_HEALTH_LYME_GAP_ATLAS.PRESENTATION" not in sql for sql in cursor.statements)


def test_invalid_configured_presentation_identifier_fails_closed() -> None:
    settings = ApiSettings(snowflake_presentation_database="PROD; DROP DATABASE X")

    with pytest.raises(AtlasDataUnavailableError, match="unavailable"):
        SnowflakeAtlasRepository(settings).load_snapshot()
