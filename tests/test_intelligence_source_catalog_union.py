"""Execute the catalog union over real rows in SQLite's common SQL subset.

Only Snowflake's namespace and aggregate spellings are adapted. This is not
Snowflake engine, grant or intended-service acceptance evidence.
"""

import hashlib
import json
import sqlite3

import pytest

from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import FeedService, FeedSources, SnowflakeFeedRepository


def _catalog(monkeypatch, projection, published):
    """Run the sources query. `published` rows are (view, source, version, organization, trust)."""

    database = sqlite3.connect(":memory:")

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

    def insert(view, source, version, organization, trust):
        database.execute(
            f"INSERT INTO {view} VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"item-{source}-{version}",
                f"revision-{version}",
                source,
                version,
                "2026-10-22T00:00:00Z",
                "a" * 64,
                organization,
                trust,
                "2026-10-21T00:00:00Z",
            ),
        )

    for view, source, version, organization, trust in published:
        insert(view, source, version, organization, trust)
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, statement, params, *, timeout):
            assert timeout > 0
            statements.append(statement)
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
            pass

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(
        "lyme_gap_atlas_api.intelligence_feed.read_connection", lambda _: Connection()
    )
    service = FeedService(
        SnowflakeFeedRepository(
            ApiSettings(
                snowflake_presentation_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD",
                intelligence_feed_projection_version=projection,
            )
        )
    )
    return database, service, statements


@pytest.mark.parametrize("projection", ["v1", "v2"])
def test_union_returns_real_legacy_v2_shared_and_versioned_source_rows(monkeypatch, projection):
    database, service, statements = _catalog(
        monkeypatch,
        projection,
        (
            ("INTELLIGENCE_FEED_V", "source-a", 1, "Legacy publisher", "official_public_health"),
            ("INTELLIGENCE_FEED_V2", "source-b", 1, "V2 publisher", "research_index"),
            ("INTELLIGENCE_FEED_V", "source-c", 1, "Shared publisher", "official_public_health"),
            ("INTELLIGENCE_FEED_V2", "source-c", 1, "Shared publisher", "official_public_health"),
            ("INTELLIGENCE_FEED_V2", "source-c", 2, "Shared publisher", "official_public_health"),
        ),
    )
    first = service.search("sources", None, None, None, 2, None)
    assert isinstance(first, FeedSources)
    assert first.meta.next_page_token
    second = service.search("sources", None, None, None, 2, first.meta.next_page_token)
    assert isinstance(second, FeedSources)
    assert second.meta.next_page_token is None
    rows = [*first.data, *second.data]
    assert [(row.source_id, row.registry_version) for row in rows] == [
        ("source-a", 1),
        ("source-b", 1),
        ("source-c", 2),
    ]
    assert [row.organization for row in rows] == [
        "Legacy publisher",
        "V2 publisher",
        "Shared publisher",
    ]
    assert all(row.operational_state == "unavailable" for row in rows)
    assert first.source_catalog_complete is second.source_catalog_complete is False
    assert first.meta.source_health_available is False
    assert len(statements) == 6
    database.close()


@pytest.mark.parametrize("projection", ["v1", "v2"])
def test_sources_catalog_keeps_highest_registry_version_per_source_id(monkeypatch, projection):
    """Same source in v1 and v2, plus sources that appear once, including a version tie."""

    other_sources = (
        ("INTELLIGENCE_FEED_V", "cdc-newsroom", 1, "CDC", "official_public_health"),
        ("INTELLIGENCE_FEED_V2", "nih-news-releases", 1, "NIH", "official_public_health"),
        ("INTELLIGENCE_FEED_V", "promed-mail", 1, "ProMED", "reviewed_other"),
        ("INTELLIGENCE_FEED_V2", "promed-mail", 1, "ProMED", "reviewed_other"),
        ("INTELLIGENCE_FEED_V2", "eurekalert", 1, "EurekAlert", "research_index"),
    )
    database, service, statements = _catalog(
        monkeypatch,
        projection,
        (
            *other_sources,
            (
                "INTELLIGENCE_FEED_V",
                "cdc-eid-ahead-of-print",
                1,
                "CDC",
                "official_public_health",
            ),
            (
                "INTELLIGENCE_FEED_V2",
                "cdc-eid-ahead-of-print",
                2,
                "CDC EID",
                "research_index",
            ),
            ("INTELLIGENCE_FEED_V2", "cdc-eid-ahead-of-print", 2, "CDC EID", "research_index"),
            ("INTELLIGENCE_FEED_V", "tied-source", 2, "Zed publisher", "research_index"),
            ("INTELLIGENCE_FEED_V2", "tied-source", 2, "Alpha publisher", "official_public_health"),
            ("INTELLIGENCE_FEED_V", "tied-source", 1, "Aaa publisher", "reviewed_other"),
        ),
    )
    page = service.search("sources", None, None, None, 100, None)
    assert isinstance(page, FeedSources)
    assert page.meta.next_page_token is None
    rows = [
        (
            row.source_id,
            row.registry_version,
            row.organization,
            row.reviewed_trust_classification,
            row.operational_state,
        )
        for row in page.data
    ]
    assert rows == [
        ("cdc-eid-ahead-of-print", 2, "CDC EID", "research_index", "unavailable"),
        ("cdc-newsroom", 1, "CDC", "official_public_health", "unavailable"),
        ("eurekalert", 1, "EurekAlert", "research_index", "unavailable"),
        ("nih-news-releases", 1, "NIH", "official_public_health", "unavailable"),
        ("promed-mail", 1, "ProMED", "reviewed_other", "unavailable"),
        ("tied-source", 2, "Alpha publisher", "official_public_health", "unavailable"),
    ]
    assert [row[:4] for row in rows if row[0] not in {"cdc-eid-ahead-of-print", "tied-source"}] == [
        ("cdc-newsroom", 1, "CDC", "official_public_health"),
        ("eurekalert", 1, "EurekAlert", "research_index"),
        ("nih-news-releases", 1, "NIH", "official_public_health"),
        ("promed-mail", 1, "ProMED", "reviewed_other"),
    ]
    assert len({row[0] for row in rows}) == len(rows)
    assert len(statements) == 3
    database.close()
