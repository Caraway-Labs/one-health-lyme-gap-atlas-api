"""Source operational state from PRESENTATION.INTELLIGENCE_SOURCE_HEALTH_V1."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import (
    SOURCE_HEALTH_CACHE_TTL_SECONDS,
    SOURCE_HEALTH_NEGATIVE_CACHE_TTL_SECONDS,
    FeedService,
    SnowflakeFeedRepository,
    SourceHealth,
    select_latest_source_health,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/intelligence/source_health_v1.json").read_text()
)
SECRET = "password=hunter2 run_id=secret-run"


def test_latest_health_row_wins_and_drops_free_text() -> None:
    """A newer unusable state hides an older healthy row; free text is omitted."""

    rows = [
        ("cdc-outbreaks", "2026-10-25T12:00:00Z", None, "healthy"),
        ("cdc-outbreaks", "2026-10-25T16:00:00Z", "see the operator log", "UNKNOWN"),
        ("cdc-mmwr", "2026-10-25T15:00:00Z", None, "healthy"),
        (
            "cdc-mmwr",
            "2026-10-25T15:00:00Z",
            "INTELLIGENCE_SOURCE_FETCH_FAILED",
            "failing",
        ),
        ("state-epi", "2026-10-22T12:00:00Z", "operator note run 40140275945", "stale"),
        (
            "cdc-outbreaks-datetime",
            datetime(2026, 10, 25, 14, 51, tzinfo=UTC),
            None,
            "HEALTHY",
        ),
    ]
    selected = select_latest_source_health(rows)
    assert "cdc-outbreaks" not in selected
    assert selected["cdc-mmwr"] == SourceHealth(
        operational_state="failing",
        last_completed_at="2026-10-25T15:00:00Z",
        last_error_code="INTELLIGENCE_SOURCE_FETCH_FAILED",
    )
    assert selected["state-epi"].last_error_code is None
    assert selected["state-epi"].operational_state == "stale"
    assert selected["cdc-outbreaks-datetime"].last_completed_at == "2026-10-25T14:51:00Z"
    assert SECRET not in repr(selected)


def _catalog(monkeypatch, *, health_rows, fail_health: bool = False, ceiling: int = 10_000):
    database = sqlite3.connect(":memory:", check_same_thread=False)

    class StateHash:
        def __init__(self):
            self.value = 0

        def step(self, *values):
            digest = hashlib.sha256(json.dumps(values).encode()).digest()
            self.value ^= int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)

        def finalize(self):
            return self.value

    database.create_aggregate("HASH_AGG", -1, StateHash)
    for view in ("INTELLIGENCE_FEED_V", "INTELLIGENCE_FEED_V2"):
        database.execute(
            f"CREATE TABLE {view} (ITEM_ID TEXT, REVISION_ID TEXT, SOURCE_ID TEXT, "
            "REGISTRY_VERSION INTEGER, FETCHED_AT TEXT, CONTENT_SHA256 TEXT, "
            "ORGANIZATION TEXT, REVIEWED_TRUST_CLASSIFICATION TEXT, PUBLISHED_AT TEXT)"
        )
    if health_rows is not None:
        database.execute(
            "CREATE TABLE INTELLIGENCE_SOURCE_HEALTH_V1 ("
            "SOURCE_ID TEXT, LAST_COMPLETED_AT TEXT, LAST_STATUS TEXT, "
            "LAST_ERROR_CODE TEXT, ITEMS_CURRENT INTEGER, STATE TEXT, RUN_ID TEXT)"
        )
        for row in health_rows:
            database.execute(
                "INSERT INTO INTELLIGENCE_SOURCE_HEALTH_V1 VALUES (?,?,?,?,?,?,?)",
                (
                    row["source_id"],
                    row["last_completed_at"],
                    row["last_status"],
                    row["last_error_code"],
                    row["items_current"],
                    row["state"],
                    row["run_id"],
                ),
            )
    for source, version, organization, trust in FIXTURE["sources"]:
        database.execute(
            "INSERT INTO INTELLIGENCE_FEED_V VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"item-{source}",
                "a" * 64,
                source,
                version,
                "2026-10-22T00:00:00Z",
                "b" * 64,
                organization,
                trust,
                "2026-10-21T00:00:00Z",
            ),
        )
    statements: list[tuple[str, tuple[Any, ...]]] = []
    clock = {"now": 0.0}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, statement, params, *, timeout):
            assert timeout > 0
            statements.append((statement, tuple(params)))
            if fail_health and "INTELLIGENCE_SOURCE_HEALTH_V1" in statement:
                raise RuntimeError(SECRET)
            sql = statement.replace('"ONE_HEALTH_LYME_GAP_ATLAS_PROD"."PRESENTATION".', "")
            sql = sql.replace(
                "COUNT(DISTINCT SOURCE_ID, REGISTRY_VERSION)",
                "COUNT(DISTINCT JSON_ARRAY(SOURCE_ID, REGISTRY_VERSION))",
            )
            self.result = database.execute(sql.replace("%s", "?"), params)

        def fetchone(self):
            return self.result.fetchone()

        def fetchall(self):
            return self.result.fetchall()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(
        "lyme_gap_atlas_api.intelligence_feed.read_connection", lambda _: Connection()
    )
    repository = SnowflakeFeedRepository(
        ApiSettings(
            snowflake_presentation_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD",
            public_query_result_ceiling=ceiling,
        ),
        clock=lambda: clock["now"],
    )
    return database, repository, statements, clock


def _health_statements(statements):
    return [item for item in statements if "INTELLIGENCE_SOURCE_HEALTH_V1" in item[0]]


def test_sources_apply_fixture_health_without_duplicating_rows(monkeypatch) -> None:
    """Healthy, stale, failing, and missing rows stay one catalog row per source."""

    database, repository, statements, clock = _catalog(
        monkeypatch, health_rows=FIXTURE["health_rows"]
    )
    app = TestClient(
        create_app(
            settings=ApiSettings(intelligence_feed_read_enabled=True),
            intelligence_repository=repository,
        )
    )
    first = app.get("/v1/intelligence/sources", params={"page_size": 2})
    assert first.status_code == 200
    token = first.json()["meta"]["next_page_token"]
    second = app.get("/v1/intelligence/sources", params={"page_size": 2, "page_token": token})
    third = app.get(
        "/v1/intelligence/sources",
        params={"page_size": 2, "page_token": second.json()["meta"]["next_page_token"]},
    )
    assert second.status_code == third.status_code == 200
    rows = first.json()["data"] + second.json()["data"] + third.json()["data"]
    assert [row["source_id"] for row in rows] == [
        "cdc-eid-ahead-of-print",
        "cdc-mmwr",
        "cdc-outbreaks",
        "state-epi",
        "tie-source",
    ]
    assert len({row["source_id"] for row in rows}) == len(rows)
    by_id = {row["source_id"]: row for row in rows}
    missing = by_id["cdc-eid-ahead-of-print"]
    assert missing["operational_state"] == "unavailable"
    assert missing["diagnostic"] == "SOURCE_HEALTH_NOT_PUBLISHED"
    assert missing["last_completed_at"] is None
    assert missing["last_error_code"] is None
    assert by_id["cdc-outbreaks"]["operational_state"] == "healthy"
    assert by_id["cdc-outbreaks"]["last_completed_at"] == "2026-10-25T14:51:00Z"
    assert by_id["cdc-outbreaks"]["diagnostic"] is None
    assert by_id["cdc-outbreaks"]["last_error_code"] is None
    assert by_id["cdc-mmwr"]["operational_state"] == "failing"
    assert by_id["cdc-mmwr"]["last_error_code"] == "INTELLIGENCE_SOURCE_FETCH_FAILED"
    assert by_id["cdc-mmwr"]["last_completed_at"] == "2026-10-25T15:00:00Z"
    assert by_id["state-epi"]["operational_state"] == "stale"
    assert by_id["state-epi"]["last_completed_at"] == "2026-10-22T12:00:00Z"
    assert by_id["state-epi"]["last_error_code"] is None
    assert by_id["tie-source"]["operational_state"] == "failing"
    assert by_id["tie-source"]["last_error_code"] == "FEED_MALFORMED"
    body = first.text + second.text + third.text
    assert "should-not-leak" not in body
    assert "operator note" not in body
    assert "40140275945" not in body
    assert "not-in-catalog" not in body
    assert all(
        page.json()["meta"]["source_health_available"] is True for page in (first, second, third)
    )
    assert all(page.json()["source_catalog_complete"] is False for page in (first, second, third))
    health_sql = _health_statements(statements)
    assert len(health_sql) == 1
    assert health_sql[0][1] == (10_001,)
    assert "RUN_ID" not in health_sql[0][0]
    assert "LAST_STATUS" not in health_sql[0][0]
    assert "ITEMS_CURRENT" not in health_sql[0][0]
    clock["now"] = SOURCE_HEALTH_CACHE_TTL_SECONDS
    app.get("/v1/intelligence/sources", params={"page_size": 2})
    assert len(_health_statements(statements)) == 2
    database.close()


def test_health_read_raises_and_sources_stay_unavailable(capfd) -> None:
    class Repository:
        def read(self, kind, source_id, start, end, offset, size):
            assert kind == "sources"
            return "stable", [
                ("cdc-outbreaks", 1, "CDC", "official_public_health"),
                ("cdc-mmwr", 1, "CDC", "official_public_health"),
            ]

        def read_source_health(self):
            raise RuntimeError(SECRET)

    response = TestClient(
        create_app(
            settings=ApiSettings(intelligence_feed_read_enabled=True),
            intelligence_repository=Repository(),
        )
    ).get("/v1/intelligence/sources")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source_health_available"] is False
    assert [row["source_id"] for row in body["data"]] == ["cdc-outbreaks", "cdc-mmwr"]
    assert all(row["operational_state"] == "unavailable" for row in body["data"])
    assert all(row["diagnostic"] == "SOURCE_HEALTH_NOT_PUBLISHED" for row in body["data"])
    assert SECRET not in response.text
    captured = capfd.readouterr()
    assert '"error_class": "RuntimeError"' in captured.err
    assert SECRET not in captured.err


def test_missing_health_view_is_negative_cached(monkeypatch, caplog) -> None:
    database, repository, statements, clock = _catalog(
        monkeypatch, health_rows=None, fail_health=True
    )
    caplog.set_level("WARNING", logger="lyme_gap_atlas_api.intelligence_feed")
    service = FeedService(repository)
    for _ in range(2):
        page = service.search("sources", None, None, None, 100, None)
        assert all(row.operational_state == "unavailable" for row in page.data)
        assert page.meta.source_health_available is False
    assert len(_health_statements(statements)) == 1
    clock["now"] = SOURCE_HEALTH_NEGATIVE_CACHE_TTL_SECONDS - 1
    service.search("sources", None, None, None, 100, None)
    assert len(_health_statements(statements)) == 1
    clock["now"] = SOURCE_HEALTH_NEGATIVE_CACHE_TTL_SECONDS
    service.search("sources", None, None, None, 100, None)
    assert len(_health_statements(statements)) == 2
    assert any(
        getattr(record, "context", {}).get("error_class") == "RuntimeError"
        for record in caplog.records
    )
    assert SECRET not in caplog.text
    database.close()


def test_truncated_health_read_does_not_publish_partial_state(monkeypatch, caplog) -> None:
    database, repository, statements, _clock = _catalog(
        monkeypatch, health_rows=FIXTURE["health_rows"], ceiling=5
    )
    caplog.set_level("WARNING", logger="lyme_gap_atlas_api.intelligence_feed")
    page = FeedService(repository).search("sources", None, None, None, 100, None)
    assert all(row.operational_state == "unavailable" for row in page.data)
    assert page.meta.source_health_available is False
    assert any(
        getattr(record, "context", {}).get("error_class") == "SourceHealthResultTruncated"
        for record in caplog.records
    )
    assert len(_health_statements(statements)) == 1
    FeedService(repository).search("sources", None, None, None, 100, None)
    assert len(_health_statements(statements)) == 1
    database.close()


def test_sources_schema_publishes_health_without_changing_feed_meta() -> None:
    schema = create_app(settings=ApiSettings()).first_party_openapi()
    feed_meta = schema["components"]["schemas"]["FeedMeta"]["properties"]["source_health_available"]
    assert feed_meta["const"] is False
    sources = schema["paths"]["/v1/intelligence/sources"]["get"]
    assert "INTELLIGENCE_SOURCE_HEALTH_V1" in sources["description"]
    assert "/v1/intelligence/sources" not in create_app(settings=ApiSettings()).openapi()["paths"]
    properties = schema["components"]["schemas"]["FeedSource"]["properties"]
    assert set(properties) >= {
        "source_id",
        "registry_version",
        "organization",
        "reviewed_trust_classification",
        "operational_state",
        "diagnostic",
        "last_completed_at",
        "last_error_code",
    }
    assert properties["operational_state"]["enum"] == ["healthy", "stale", "failing", "unavailable"]
