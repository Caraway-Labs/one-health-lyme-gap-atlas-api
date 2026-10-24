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


@pytest.mark.parametrize("projection", ["v1", "v2"])
def test_union_returns_real_legacy_v2_shared_and_versioned_source_rows(monkeypatch, projection):
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

    insert("INTELLIGENCE_FEED_V", "source-a", 1, "Legacy publisher", "official_public_health")
    insert("INTELLIGENCE_FEED_V2", "source-b", 1, "V2 publisher", "research_index")
    insert("INTELLIGENCE_FEED_V", "source-c", 1, "Shared publisher", "official_public_health")
    insert("INTELLIGENCE_FEED_V2", "source-c", 1, "Shared publisher", "official_public_health")
    insert("INTELLIGENCE_FEED_V2", "source-c", 2, "Shared publisher", "official_public_health")
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

    monkeypatch.setattr("lyme_gap_atlas_api.intelligence_feed.connect", lambda _: Connection())
    service = FeedService(
        SnowflakeFeedRepository(
            ApiSettings(
                snowflake_presentation_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD",
                intelligence_feed_projection_version=projection,
            )
        )
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
        ("source-c", 1),
        ("source-c", 2),
    ]
    assert [row.organization for row in rows] == [
        "Legacy publisher",
        "V2 publisher",
        "Shared publisher",
        "Shared publisher",
    ]
    assert all(row.operational_state == "unavailable" for row in rows)
    assert first.source_catalog_complete is second.source_catalog_complete is False
    assert first.meta.source_health_available is False
    assert len(statements) == 6
    database.close()
