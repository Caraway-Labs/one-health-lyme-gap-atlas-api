"""Newest-first feed order and the per-process ABSTRACT_SUPPRESSED lookup cache.

Latency numbers in this module are simulated on a fake cursor. They are not
Snowflake timings.
"""

import hashlib
import json
import random
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from _pooled import lend
from fastapi.testclient import TestClient
from test_intelligence_feed_read import Repository

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import FEED_LIST_ORDER, SnowflakeFeedRepository
from lyme_gap_atlas_api.public_tokens import encode

FIXTURE = Path(__file__).parent / "fixtures/intelligence/rss-item.json"
SETTINGS = ApiSettings(
    intelligence_feed_read_enabled=True,
    snowflake_presentation_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD",
)
# One INFORMATION_SCHEMA lookup is charged 900 ms. Each other statement is
# charged 167 ms, so three of them match the previous ~0.5 s feed read.
SCHEMA_SIMULATED_MS = 900
OTHER_SIMULATED_MS = 167
TIE_AT = "2026-03-15T15:04:05Z"


def _parse_publish_time(value: object) -> datetime | None:
    """Approximate TRY_TO_TIMESTAMP_TZ for the timestamp strings these tests use."""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if " " in text and "T" not in text:
        pieces = text.split(" ")
        if len(pieces) == 2:
            text = f"{pieces[0]}T{pieces[1]}"
        elif len(pieces) == 3:
            zone = pieces[2]
            if len(zone) == 5 and zone[0] in "+-" and zone[1:].isdigit():
                zone = f"{zone[:3]}:{zone[3:]}"
            text = f"{pieces[0]}T{pieces[1]}{zone}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _feed_sort_key(row: tuple[Any, ...]) -> tuple[Any, ...]:
    parsed = _parse_publish_time(row[4])
    return (
        0 if row[9] == "present" else 1,
        parsed is None,
        -parsed.timestamp() if parsed is not None else 0.0,
        row[0],
        row[1],
        row[12],
        row[13],
    )


def _bind(monkeypatch: pytest.MonkeyPatch, cursor: Any) -> None:
    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def cursor(self) -> Any:
            return cursor

    monkeypatch.setattr(
        "lyme_gap_atlas_api.intelligence_feed.read_connection",
        lend(lambda settings: Connection()),
    )


class _ContextCursor:
    def __enter__(self) -> "_ContextCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class OrderingCursor(_ContextCursor):
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.schema_queries = 0
        self._mode = ""
        self._state: tuple[int, int] = (0, 0)
        self._page: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...], *, timeout: int) -> None:
        self.calls.append((sql, params))
        if "INFORMATION_SCHEMA" in sql:
            self.schema_queries += 1
            self._mode = "schema"
            return
        if FEED_LIST_ORDER not in sql and "HASH_AGG" not in sql:
            raise AssertionError(sql)
        bound = params[:-2] if " LIMIT " in sql else params
        matched = self._filter(sql, bound)
        if "HASH_AGG" in sql:
            self._mode = "state"
            self._state = (len(matched), 7)
            return
        ordered = sorted(matched, key=_feed_sort_key)
        limit, offset = params[-2], params[-1]
        self._mode = "page"
        self._page = ordered[offset : offset + limit]

    def _filter(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        rows = list(self.rows)
        index = 0
        if "SOURCE_ID = %s" in sql:
            source_id = params[index]
            index += 1
            rows = [row for row in rows if row[12] == source_id]
        if "SUBSTR(PUBLISHED_AT, 1, 10) >= %s" in sql:
            start = params[index]
            index += 1
            rows = [row for row in rows if isinstance(row[4], str) and row[4][:10] >= start]
        if "SUBSTR(PUBLISHED_AT, 1, 10) <= %s" in sql:
            end = params[index]
            rows = [row for row in rows if isinstance(row[4], str) and row[4][:10] <= end]
        return rows

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._mode == "schema":
            return None
        return self._state

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._page)


class SchemaCursor(_ContextCursor):
    def __init__(self, *, present: bool = False, fail: bool = False) -> None:
        self.present = present
        self.fail = fail
        self.schema_queries = 0
        self.calls: list[str] = []

    def execute(self, sql: str, params: tuple[Any, ...], *, timeout: int) -> None:
        self.calls.append(sql)
        if "INFORMATION_SCHEMA" in sql:
            self.schema_queries += 1
            if self.fail:
                raise RuntimeError("information schema unavailable")

    def fetchone(self) -> tuple[Any, ...] | None:
        if self.calls and "INFORMATION_SCHEMA" in self.calls[-1]:
            return ("ABSTRACT_SUPPRESSED",) if self.present else None
        return (0, 1)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []


class LatencyCursor(_ContextCursor):
    """Counts INFORMATION_SCHEMA queries and adds a fixed simulated cost per query."""

    def __init__(self) -> None:
        self.schema_queries = 0
        self.simulated_ms = 0
        self._sql = ""

    def execute(self, sql: str, params: tuple[Any, ...], *, timeout: int) -> None:
        self._sql = sql
        if "INFORMATION_SCHEMA" in sql:
            self.schema_queries += 1
            self.simulated_ms += SCHEMA_SIMULATED_MS
        else:
            self.simulated_ms += OTHER_SIMULATED_MS

    def fetchone(self) -> tuple[Any, ...] | None:
        if "INFORMATION_SCHEMA" in self._sql:
            return None
        return (0, 1)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []


def _identity(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return (row[0], row[1], row[12], row[13], row[9], row[4])


def _raw(
    item: str,
    revision: str,
    published: str | None,
    state: str,
    source: str = "cdc-mmwr",
    registry: int = 1,
) -> tuple[Any, ...]:
    return (
        item,
        revision,
        "Title",
        "https://example.org/publication/fixture",
        published,
        "2026-09-30T08:00:00Z",
        {},
        [],
        "2.0.0",
        state,
        "CDC",
        {"source_family": "cdc_mmwr"},
        source,
        registry,
        "CDC",
        "official_public_health",
    )


def test_feed_orders_present_dates_before_invalid_and_breaks_exact_timestamp_ties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = {name: name * 64 for name in ("a", "b", "c", "d", "m", "n", "p", "q")}
    revision = {name: name * 64 for name in ("0", "1", "2")}
    same_instant = "2026-10-14T15:04:05Z"
    rows = [
        _raw(item["b"], revision["0"], same_instant, "present"),
        _raw(item["m"], revision["1"], "2026-12-31T00:00:00Z", "not_provided"),
        _raw(item["c"], revision["1"], "2026-10-20T00:00:00Z", "present"),
        _raw(item["q"], revision["1"], None, "withheld"),
        _raw(item["a"], revision["2"], same_instant, "present", registry=2),
        _raw(item["a"], revision["2"], same_instant, "present", source="cdc-eid"),
        _raw(item["a"], revision["1"], "2026-10-14T15:04:05+00:00", "present"),
        _raw(item["a"], revision["2"], same_instant, "present"),
        _raw(item["p"], revision["1"], "not-a-date", "invalid"),
        _raw(item["d"], revision["1"], "2026-01-02T00:00:00Z", "present"),
        _raw(item["n"], revision["1"], None, "not_provided"),
    ]
    cursor = OrderingCursor(rows)
    _bind(monkeypatch, cursor)
    repository = SnowflakeFeedRepository(SETTINGS)
    _marker, page = repository.read("feed", None, None, None, 0, 20)
    assert [_identity(row) for row in page] == [
        (item["c"], revision["1"], "cdc-mmwr", 1, "present", "2026-10-20T00:00:00Z"),
        (item["a"], revision["1"], "cdc-mmwr", 1, "present", "2026-10-14T15:04:05+00:00"),
        (item["a"], revision["2"], "cdc-eid", 1, "present", same_instant),
        (item["a"], revision["2"], "cdc-mmwr", 1, "present", same_instant),
        (item["a"], revision["2"], "cdc-mmwr", 2, "present", same_instant),
        (item["b"], revision["0"], "cdc-mmwr", 1, "present", same_instant),
        (item["d"], revision["1"], "cdc-mmwr", 1, "present", "2026-01-02T00:00:00Z"),
        (item["m"], revision["1"], "cdc-mmwr", 1, "not_provided", "2026-12-31T00:00:00Z"),
        (item["n"], revision["1"], "cdc-mmwr", 1, "not_provided", None),
        (item["p"], revision["1"], "cdc-mmwr", 1, "invalid", "not-a-date"),
        (item["q"], revision["1"], "cdc-mmwr", 1, "withheld", None),
    ]
    page_sql = next(sql for sql, _params in cursor.calls if " LIMIT " in sql)
    assert FEED_LIST_ORDER in page_sql
    assert cursor.schema_queries == 1

    _marker, filtered = repository.read("feed", "cdc-mmwr", date(2026, 6, 1), None, 0, 20)
    assert [_identity(row) for row in filtered] == [
        (item["c"], revision["1"], "cdc-mmwr", 1, "present", "2026-10-20T00:00:00Z"),
        (item["a"], revision["1"], "cdc-mmwr", 1, "present", "2026-10-14T15:04:05+00:00"),
        (item["a"], revision["2"], "cdc-mmwr", 1, "present", same_instant),
        (item["a"], revision["2"], "cdc-mmwr", 2, "present", same_instant),
        (item["b"], revision["0"], "cdc-mmwr", 1, "present", same_instant),
        (item["m"], revision["1"], "cdc-mmwr", 1, "not_provided", "2026-12-31T00:00:00Z"),
        (item["p"], revision["1"], "cdc-mmwr", 1, "invalid", "not-a-date"),
    ]
    filtered_sql, filtered_params = next(
        (sql, params) for sql, params in reversed(cursor.calls) if " LIMIT " in sql
    )
    assert "SOURCE_ID = %s" in filtered_sql
    assert "SUBSTR(PUBLISHED_AT, 1, 10) >= %s" in filtered_sql
    assert FEED_LIST_ORDER in filtered_sql
    assert filtered_params[:2] == ("cdc-mmwr", "2026-06-01")
    assert cursor.schema_queries == 1


def _http_row(
    base: dict[str, Any],
    *,
    item_id: str,
    revision_id: str,
    published_at: str | None,
    state: str,
    source_id: str,
    registry_version: int,
) -> tuple[Any, ...]:
    return (
        item_id,
        revision_id,
        "Synthetic county investigation briefing",
        base["canonical_url"],
        published_at,
        base["fetched_at"],
        {
            **base["provenance"],
            "parser_version": "rss-atom-native-v2",
            "normalization_version": "intelligence-identity-v2",
        },
        base["limitations"],
        "2.0.0",
        state,
        "CDC",
        {
            "source_family": "cdc_mmwr",
            "description": {
                "text": "Summary",
                "state": "present",
                "format": "plain_text",
                "origin": "publisher",
            },
            "categories": ["Lyme disease"],
            "authors": None,
            "abstract": None,
            "source_item_id": None,
        },
        source_id,
        registry_version,
        "CDC",
        "official_public_health",
    )


def _catalog() -> list[tuple[Any, ...]]:
    base = json.loads(FIXTURE.read_text())
    revision = "1" * 64
    rows: list[tuple[Any, ...]] = []
    tie_ids = ("d" * 64, "e" * 64, "f" * 64)
    for item_id, revision_id in (
        (tie_ids[2], revision),
        (tie_ids[1], revision),
        (tie_ids[0], "2" * 64),
        (tie_ids[0], revision),
    ):
        rows.append(
            _http_row(
                base,
                item_id=item_id,
                revision_id=revision_id,
                published_at=TIE_AT,
                state="present",
                source_id="cdc-mmwr",
                registry_version=1,
            )
        )
    scrambled = list(range(239))
    random.Random(0).shuffle(scrambled)
    for index in scrambled:
        moment = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index * 997)
        rows.append(
            _http_row(
                base,
                item_id=f"{index:064x}",
                revision_id=revision,
                published_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                state="present",
                source_id="cdc-mmwr",
                registry_version=1,
            )
        )
    for index in range(20):
        moment = datetime(2026, 4, 1, tzinfo=UTC) + timedelta(hours=index)
        rows.append(
            _http_row(
                base,
                item_id=f"{1000 + index:064x}",
                revision_id=revision,
                published_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                state="present",
                source_id="cdc-eid",
                registry_version=1,
            )
        )
    for index in range(20):
        rows.append(
            _http_row(
                base,
                item_id=f"{2000 + index:064x}",
                revision_id=revision,
                published_at=f"2025-06-{(index % 27) + 1:02d}T00:00:00Z",
                state="present",
                source_id="cdc-mmwr",
                registry_version=1,
            )
        )
    unknown = [("not_provided", 8), ("withheld", 7), ("invalid", 5)]
    unknown_index = 0
    for state, count in unknown:
        for _ in range(count):
            rows.insert(
                0,
                _http_row(
                    base,
                    item_id=f"{3000 + unknown_index:064x}",
                    revision_id=revision,
                    published_at=None,
                    state=state,
                    source_id="cdc-mmwr",
                    registry_version=1,
                ),
            )
            unknown_index += 1
    assert len(rows) == 303
    return rows


def _pages(client: TestClient, **params: object) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        query = dict(params)
        if token is not None:
            query["page_token"] = token
        response = client.get("/v1/intelligence/feed", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        collected.extend(body["data"])
        token = body["meta"]["next_page_token"]
        if token is None:
            return collected


def _identity_of(entry: dict[str, Any]) -> tuple[str, str, str, int]:
    source = entry["source"]
    return (entry["item_id"], entry["revision_id"], source["source_id"], source["registry_version"])


def test_feed_pages_303_rows_newest_first_and_keeps_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = OrderingCursor(_catalog())
    _bind(monkeypatch, cursor)
    client = TestClient(create_app(settings=SETTINGS))
    paged = _pages(client, page_size=50)
    single = _pages(client, page_size=500)
    assert len(paged) == 303
    assert len({_identity_of(entry) for entry in paged}) == 303
    assert [_identity_of(entry) for entry in paged] == [_identity_of(entry) for entry in single]
    present = [
        entry["published_at"] for entry in single if entry["publication_date_state"] == "present"
    ]
    assert present == sorted(present, reverse=True)
    seen_unknown = False
    for entry in single:
        if entry["publication_date_state"] == "present":
            assert seen_unknown is False
        else:
            seen_unknown = True
            assert entry["published_at"] is None
    ties = [entry for entry in single if entry["published_at"] == TIE_AT]
    assert [(entry["item_id"], entry["revision_id"]) for entry in ties] == [
        ("d" * 64, "1" * 64),
        ("d" * 64, "2" * 64),
        ("e" * 64, "1" * 64),
        ("f" * 64, "1" * 64),
    ]
    filtered = _pages(client, page_size=500, source_id="cdc-mmwr", published_from="2026-01-01")
    assert filtered
    assert all(entry["source"]["source_id"] == "cdc-mmwr" for entry in filtered)
    assert all(entry["publication_date_state"] == "present" for entry in filtered)
    assert all(entry["published_at"] >= "2026-01-01" for entry in filtered)
    filtered_times = [entry["published_at"] for entry in filtered]
    assert filtered_times == sorted(filtered_times, reverse=True)
    filtered_ties = [entry for entry in filtered if entry["published_at"] == TIE_AT]
    assert [(entry["item_id"], entry["revision_id"]) for entry in filtered_ties] == [
        ("d" * 64, "1" * 64),
        ("d" * 64, "2" * 64),
        ("e" * 64, "1" * 64),
        ("f" * 64, "1" * 64),
    ]
    assert cursor.schema_queries == 1


def test_previous_feed_page_token_is_rejected_and_other_kinds_keep_theirs() -> None:
    previous_feed = hashlib.sha256(
        json.dumps(["feed", None, "None", "None", 50]).encode()
    ).hexdigest()
    repository = Repository()
    client = TestClient(
        create_app(
            settings=ApiSettings(intelligence_feed_read_enabled=True),
            intelligence_repository=repository,
        )
    )
    rejected = client.get(
        "/v1/intelligence/feed",
        params={
            "page_size": 50,
            "page_token": encode({"query": previous_feed, "state": "stable", "offset": 50}),
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "INVALID_REQUEST"
    assert rejected.json()["detail"] == "Invalid page_token."
    assert repository.calls == []

    items = hashlib.sha256(json.dumps(["items", None, "None", "None", 1]).encode()).hexdigest()
    assert (
        client.get(
            "/v1/intelligence/items",
            params={
                "page_size": 1,
                "page_token": encode({"query": items, "state": repository.marker, "offset": 1}),
            },
        ).status_code
        == 200
    )
    sources = hashlib.sha256(json.dumps(["sources", None, "None", "None", 1]).encode()).hexdigest()
    assert (
        client.get(
            "/v1/intelligence/sources",
            params={
                "page_size": 1,
                "page_token": encode({"query": sources, "state": repository.marker, "offset": 1}),
            },
        ).status_code
        == 200
    )


def test_feed_description_states_the_current_order() -> None:
    description = create_app(settings=ApiSettings()).first_party_openapi()["paths"][
        "/v1/intelligence/feed"
    ]["get"]["description"]
    assert "Entries are newest first by sort time." in description
    assert (
        "An entry's sort time is its publisher date, or the time Atlas first loaded the item "
        "when the publisher date is unknown or later than that time." in description
    )
    assert "Entries without a sort time are last." in description
    assert (
        "Entries with the same sort time use a stable tie-break of item id, revision id, "
        "source id, and registry version" in description
    )
    assert "the sort time is not returned" in description
    assert (
        "When sort times are not available, entries with a publisher date are newest first "
        "and entries with an unknown publisher date are last" in description
    )
    assert "Entries with a publisher date are newest first." not in description


def test_column_lookup_is_shared_cached_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    cursor = SchemaCursor(present=True)
    _bind(monkeypatch, cursor)
    repository = SnowflakeFeedRepository(SETTINGS, clock=clock)
    for _ in range(25):
        assert repository.read("feed", None, None, None, 0, 10)[1] == []
    assert repository.read_item("a" * 64) == []
    assert cursor.schema_queries == 1
    assert ", ABSTRACT_SUPPRESSED" in cursor.calls[-1]
    clock.now = 299.0
    assert repository.read("feed", None, None, None, 0, 10)[1] == []
    assert cursor.schema_queries == 1
    clock.now = 300.0
    assert repository.read_item("b" * 64) == []
    assert cursor.schema_queries == 2


def test_failed_column_lookup_is_cached_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    cursor = SchemaCursor(fail=True)
    _bind(monkeypatch, cursor)
    repository = SnowflakeFeedRepository(SETTINGS, clock=clock)
    assert repository.read("feed", None, None, None, 0, 10)[1] == []
    assert repository.read_item("c" * 64) == []
    assert cursor.schema_queries == 1
    assert ", ABSTRACT_SUPPRESSED" not in cursor.calls[-1]
    clock.now = 59.0
    assert repository.read("feed", None, None, None, 0, 10)[1] == []
    assert cursor.schema_queries == 1
    clock.now = 60.0
    assert repository.read_item("d" * 64) == []
    assert cursor.schema_queries == 2


def test_column_lookup_is_serialized_across_feed_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _LookupGate()

    class Cursor(_ContextCursor):
        def __init__(self) -> None:
            self._sql = ""

        def execute(self, sql: str, params: tuple[Any, ...], *, timeout: int) -> None:
            self._sql = sql
            if "INFORMATION_SCHEMA" not in sql:
                return
            with gate.lock:
                gate.schema_queries += 1
                first = gate.schema_queries == 1
            if first:
                gate.entered.set()
                assert gate.release.wait(timeout=5)

        def fetchone(self) -> tuple[Any, ...] | None:
            if "INFORMATION_SCHEMA" in self._sql:
                return None
            return (0, 1)

        def fetchall(self) -> list[tuple[Any, ...]]:
            return []

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr(
        "lyme_gap_atlas_api.intelligence_feed.read_connection",
        lend(lambda settings: Connection()),
    )
    repository = SnowflakeFeedRepository(SETTINGS)

    def read() -> None:
        repository.read("feed", None, None, None, 0, 1)

    first = threading.Thread(target=read)
    first.start()
    assert gate.entered.wait(timeout=5)
    others = [threading.Thread(target=read) for _ in range(7)]
    for thread in others:
        thread.start()
    threading.Event().wait(0.05)
    assert gate.schema_queries == 1
    gate.release.set()
    first.join(timeout=5)
    for thread in others:
        thread.join(timeout=5)
    assert gate.schema_queries == 1


def test_simulated_lookup_cost_for_one_hundred_feed_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    before = LatencyCursor()
    for _ in range(100):
        before.execute("SELECT COUNT(*), HASH_AGG(ITEM_ID) FROM VIEW", (), timeout=15)
        before.execute(
            "SELECT COLUMN_NAME FROM DB.INFORMATION_SCHEMA.COLUMNS",
            (),
            timeout=15,
        )
        before.execute("SELECT ITEM_ID FROM VIEW LIMIT %s OFFSET %s", (11, 0), timeout=15)
        before.execute("SELECT COUNT(*), HASH_AGG(ITEM_ID) FROM VIEW", (), timeout=15)

    after = LatencyCursor()
    _bind(monkeypatch, after)
    repository = SnowflakeFeedRepository(SETTINGS)
    for _ in range(100):
        repository.read("feed", None, None, None, 0, 10)

    before_per_request_ms = SCHEMA_SIMULATED_MS + 3 * OTHER_SIMULATED_MS
    after_per_request_ms = (SCHEMA_SIMULATED_MS + 300 * OTHER_SIMULATED_MS) / 100
    assert before.schema_queries == 100
    assert after.schema_queries == 1
    assert before.simulated_ms == 100 * before_per_request_ms
    assert after.simulated_ms == 100 * after_per_request_ms
    assert before_per_request_ms == 1401
    assert after_per_request_ms == 510


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _LookupGate:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.schema_queries = 0
