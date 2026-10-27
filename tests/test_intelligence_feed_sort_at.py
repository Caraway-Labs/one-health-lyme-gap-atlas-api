"""Feed order follows FEED_SORT_AT when the governed view has it (data#871).

Without the column the feed keeps FEED_LIST_ORDER byte for byte. One cached
INFORMATION_SCHEMA lookup decides, so the order switches on its own after the
data release, and a failed lookup keeps the current order instead of failing.
"""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from _feed_skip import ROW_SKIPPED, skip_log
from fastapi.testclient import TestClient
from test_intelligence_feed_ordering import (
    FIXTURE,
    SETTINGS,
    OrderingCursor,
    _bind,
    _Clock,
    _http_row,
    _parse_publish_time,
)

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.intelligence_feed import (
    FEED_LIST_ORDER,
    FEED_SORT_AT_ORDER,
    SnowflakeFeedRepository,
)
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError

LEGACY_ORDER = (
    "IFF(FIELD_STATES:published_at::VARCHAR = 'present', 0, 1), "
    "TRY_TO_TIMESTAMP_TZ(PUBLISHED_AT) DESC NULLS LAST, "
    "ITEM_ID, REVISION_ID, SOURCE_ID, REGISTRY_VERSION"
)
BOTH = frozenset({"ABSTRACT_SUPPRESSED", "FEED_SORT_AT"})
SORT_ONLY = frozenset({"FEED_SORT_AT"})
UNSAFE_URL = "javascript:alert('private-link')"


def _key(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return (row[0], row[1], row[12], row[13])


class SortCursor(OrderingCursor):
    """FEED_V2 fake that sorts by FEED_SORT_AT when the SQL asks for it.

    FEED_SORT_AT follows the data#871 definition: the publisher date, or the row's
    first-loaded time when the publisher date is missing, unparseable, or later.
    """

    def __init__(
        self,
        rows: list[tuple[Any, ...]],
        loaded: dict[str, datetime],
        *,
        columns: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(rows)
        self.loaded = loaded
        self.columns = columns
        self.fail_lookup = False
        self.fail_sort_reads = False
        self.suppressed = False

    def sort_at(self, row: tuple[Any, ...]) -> datetime:
        first = self.loaded[row[0]]
        published = _parse_publish_time(row[4])
        return first if published is None or published > first else published

    def execute(self, sql: str, params: tuple[Any, ...], *, timeout: int) -> None:
        selected = sql.split(" FROM ")[0]
        if "FEED_SORT_AT" in selected:
            raise AssertionError("FEED_SORT_AT must never be selected")
        self.suppressed = "ABSTRACT_SUPPRESSED" in selected
        if "WHERE ITEM_ID = %s" in sql:
            self.calls.append((sql, params))
            self._mode = "page"
            self._page = [row for row in self.rows if row[0] == params[0]][:1]
            return
        if FEED_SORT_AT_ORDER not in sql:
            if "INFORMATION_SCHEMA" in sql and self.fail_lookup:
                self.calls.append((sql, params))
                self.schema_queries += 1
                raise RuntimeError("information schema unavailable")
            super().execute(sql, params, timeout=timeout)
            return
        self.calls.append((sql, params))
        assert "FEED_SORT_AT" in self.columns, "ordered by a column the view does not have"
        assert sql.split(" ORDER BY ")[1] == f"{FEED_SORT_AT_ORDER} LIMIT %s OFFSET %s"
        if self.fail_sort_reads:
            raise RuntimeError("invalid identifier FEED_SORT_AT")
        matched = self._filter(sql, params[:-2])
        ordered = sorted(matched, key=lambda row: (-self.sort_at(row).timestamp(), *_key(row)))
        limit, offset = params[-2], params[-1]
        self._mode = "page"
        self._page = ordered[offset : offset + limit]

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._mode == "schema":
            return (",".join(sorted(self.columns)),)
        return self._state

    def fetchall(self) -> list[tuple[Any, ...]]:
        if self.suppressed:
            return [(*row[:12], False, *row[12:]) for row in self._page]
        return list(self._page)

    def page_sql(self) -> list[str]:
        return [sql for sql, _params in self.calls if " LIMIT " in sql]


def _row(
    item: str,
    published: str | None,
    state: str = "present",
    *,
    url: str | None = None,
) -> tuple[Any, ...]:
    base = json.loads(FIXTURE.read_text())
    if url is not None:
        base["canonical_url"] = url
    return _http_row(
        base,
        item_id=item * 64 if len(item) == 1 else item,
        revision_id="1" * 64,
        published_at=published,
        state=state,
        source_id="cdc-mmwr",
        registry_version=1,
    )


def _ordering_fixture() -> tuple[list[tuple[Any, ...]], dict[str, datetime]]:
    rows_and_loads = [
        (_row("a", "2026-10-20T00:00:00Z"), datetime(2026, 10, 21, tzinfo=UTC)),
        # Publisher date in the future: sorted at its first load.
        (_row("f", "2026-12-31T00:00:00Z"), datetime(2026, 10, 22, tzinfo=UTC)),
        # No publisher date: sorted at its first load, which is the newest load.
        (_row("e", None, "not_provided"), datetime(2026, 10, 25, tzinfo=UTC)),
        (_row("d", None, "withheld"), datetime(2026, 10, 1, tzinfo=UTC)),
        (_row("b", "2026-01-02T00:00:00Z"), datetime(2026, 1, 3, tzinfo=UTC)),
        (_row("c", "2026-10-24T00:00:00Z"), datetime(2026, 10, 24, 6, tzinfo=UTC)),
    ]
    return [row for row, _ in rows_and_loads], {row[0]: load for row, load in rows_and_loads}


def _client(repository: SnowflakeFeedRepository) -> TestClient:
    return TestClient(create_app(settings=SETTINGS, intelligence_repository=repository))


def _feed(client: TestClient, **params: object) -> Any:
    return client.get("/v1/intelligence/feed", params=params)


def test_legacy_order_constant_is_unchanged() -> None:
    assert FEED_LIST_ORDER == LEGACY_ORDER
    assert FEED_SORT_AT_ORDER == (
        "FEED_SORT_AT DESC NULLS LAST, ITEM_ID, REVISION_ID, SOURCE_ID, REGISTRY_VERSION"
    )


@pytest.mark.parametrize("columns", [frozenset(), frozenset({"ABSTRACT_SUPPRESSED"})])
def test_without_the_column_sql_and_marker_are_todays(
    monkeypatch: pytest.MonkeyPatch, columns: frozenset[str]
) -> None:
    rows, loaded = _ordering_fixture()
    cursor = SortCursor(rows, loaded, columns=columns)
    _bind(monkeypatch, cursor)
    marker, page = SnowflakeFeedRepository(SETTINGS).read("feed", None, None, None, 0, 20)
    [statement] = cursor.page_sql()
    assert statement.endswith(f" ORDER BY {LEGACY_ORDER} LIMIT %s OFFSET %s")
    assert all("FEED_SORT_AT" not in sql for sql, _params in cursor.calls)
    # Same marker as before this change: tokens issued today stay valid.
    assert marker == hashlib.sha256(json.dumps([len(rows), 7]).encode()).hexdigest()
    # Today's order: present dates newest first (the future date on top), unknown last.
    assert [row[0][0] for row in page] == ["f", "c", "a", "b", "d", "e"]


def test_with_the_column_nothing_sorts_above_the_newest_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, loaded = _ordering_fixture()
    cursor = SortCursor(rows, loaded, columns=BOTH)
    _bind(monkeypatch, cursor)
    repository = SnowflakeFeedRepository(SETTINGS)
    marker, page = repository.read("feed", None, None, None, 0, 20)
    [statement] = cursor.page_sql()
    assert statement.endswith(f" ORDER BY {FEED_SORT_AT_ORDER} LIMIT %s OFFSET %s")
    assert "IFF(FIELD_STATES" not in statement and LEGACY_ORDER not in statement
    assert "ABSTRACT_SUPPRESSED" in statement.split(" FROM ")[0]
    assert marker == (
        hashlib.sha256(json.dumps([[len(rows), 7], FEED_SORT_AT_ORDER]).encode()).hexdigest()
    )
    assert [row[0][0] for row in page] == ["e", "c", "f", "a", "d", "b"]
    newest_load = max(loaded.values())
    assert all(cursor.sort_at(row) <= newest_load for row in rows)

    body = _feed(_client(repository), page_size=20).json()
    assert [entry["item_id"][0] for entry in body["data"]] == ["e", "c", "f", "a", "d", "b"]
    by_item = {entry["item_id"][0]: entry for entry in body["data"]}
    # Cards keep the article's own date and date state.
    assert by_item["f"]["published_at"] == "2026-12-31T00:00:00Z"
    assert by_item["f"]["publication_date_state"] == "present"
    assert by_item["e"]["published_at"] is None
    assert by_item["e"]["publication_date_state"] == "not_provided"
    assert all("feed_sort_at" not in json.dumps(entry).lower() for entry in body["data"])


def test_with_the_column_filters_and_by_id_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, loaded = _ordering_fixture()
    cursor = SortCursor(rows, loaded, columns=SORT_ONLY)
    _bind(monkeypatch, cursor)
    repository = SnowflakeFeedRepository(SETTINGS)
    _marker, page = repository.read(
        "feed", "cdc-mmwr", date(2026, 10, 1), date(2026, 10, 31), 0, 20
    )
    statement, params = next((sql, p) for sql, p in cursor.calls if " LIMIT " in sql)
    assert "SOURCE_ID = %s" in statement
    assert "SUBSTR(PUBLISHED_AT, 1, 10) >= %s" in statement
    assert "SUBSTR(PUBLISHED_AT, 1, 10) <= %s" in statement
    assert FEED_SORT_AT_ORDER in statement
    assert params == ("cdc-mmwr", "2026-10-01", "2026-10-31", 21, 0)
    # Filters still use the article's date: the future and undated rows are excluded.
    assert [row[0][0] for row in page] == ["c", "a"]
    assert "ABSTRACT_SUPPRESSED" not in statement.split(" FROM ")[0]

    cursor.calls.clear()
    repository.read_item("a" * 64)
    [item_sql] = [sql for sql, _params in cursor.calls if "INFORMATION_SCHEMA" not in sql]
    assert "ORDER BY FETCHED_AT DESC, REVISION_ID DESC LIMIT 1" in item_sql
    assert "FEED_SORT_AT" not in item_sql


def test_lookup_is_cached_and_switches_after_the_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, loaded = _ordering_fixture()
    cursor = SortCursor(rows, loaded)
    _bind(monkeypatch, cursor)
    clock = _Clock()
    repository = SnowflakeFeedRepository(SETTINGS, clock=clock)

    def order() -> str:
        repository.read("feed", None, None, None, 0, 20)
        return "sort_at" if FEED_SORT_AT_ORDER in cursor.page_sql()[-1] else "legacy"

    assert order() == "legacy"
    cursor.columns = BOTH  # the data release lands
    clock.now = 299.0
    assert order() == "legacy"
    assert cursor.schema_queries == 1
    clock.now = 300.0
    assert order() == "sort_at"
    assert cursor.schema_queries == 2
    repository.read_item("a" * 64)
    assert cursor.schema_queries == 2
    cursor.columns = frozenset()  # the release is rolled back
    clock.now = 600.0
    assert order() == "legacy"
    assert cursor.schema_queries == 3


def test_failed_lookup_keeps_the_current_order_without_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, loaded = _ordering_fixture()
    cursor = SortCursor(rows, loaded, columns=BOTH)
    cursor.fail_lookup = True
    _bind(monkeypatch, cursor)
    clock = _Clock()
    repository = SnowflakeFeedRepository(SETTINGS, clock=clock)
    client = _client(repository)

    response = _feed(client, page_size=20)
    assert response.status_code == 200
    assert FEED_LIST_ORDER in cursor.page_sql()[-1]
    assert cursor.schema_queries == 1
    cursor.fail_lookup = False
    clock.now = 59.0
    assert _feed(client, page_size=20).status_code == 200
    assert FEED_LIST_ORDER in cursor.page_sql()[-1]
    assert cursor.schema_queries == 1
    clock.now = 60.0
    assert _feed(client, page_size=20).status_code == 200
    assert FEED_SORT_AT_ORDER in cursor.page_sql()[-1]
    assert cursor.schema_queries == 2


def test_failed_sorted_read_looks_again_on_the_next_read(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, loaded = _ordering_fixture()
    cursor = SortCursor(rows, loaded, columns=SORT_ONLY)
    _bind(monkeypatch, cursor)
    repository = SnowflakeFeedRepository(SETTINGS, clock=_Clock())
    repository.read("feed", None, None, None, 0, 20)
    cursor.fail_sort_reads = True
    cursor.columns = frozenset()
    with pytest.raises(AtlasDataUnavailableError):
        repository.read("feed", None, None, None, 0, 20)
    # Within the 300 s TTL, the next read looks the column up again.
    _marker, page = repository.read("feed", None, None, None, 0, 20)
    assert len(page) == len(rows)
    assert FEED_LIST_ORDER in cursor.page_sql()[-1]
    assert cursor.schema_queries == 2


@pytest.mark.parametrize(
    ("before", "after"), [(frozenset(), BOTH), (BOTH, frozenset())], ids=["release", "rollback"]
)
def test_tokens_from_the_other_order_restart_pagination(
    monkeypatch: pytest.MonkeyPatch, before: frozenset[str], after: frozenset[str]
) -> None:
    rows, loaded = _ordering_fixture()
    cursor = SortCursor(rows, loaded, columns=before)
    _bind(monkeypatch, cursor)
    clock = _Clock()
    client = _client(SnowflakeFeedRepository(SETTINGS, clock=clock))
    first = _feed(client, page_size=2).json()
    token = first["meta"]["next_page_token"]
    assert token
    # Same order: the token keeps working.
    assert _feed(client, page_size=2, page_token=token).status_code == 200

    cursor.columns = after
    clock.now = 300.0
    rejected = _feed(client, page_size=2, page_token=token)
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "INVALID_REQUEST"
    assert rejected.json()["detail"] == "Projection changed; restart pagination."
    restarted = _feed(client, page_size=2)
    assert restarted.status_code == 200
    following = _feed(client, page_size=2, page_token=restarted.json()["meta"]["next_page_token"])
    assert following.status_code == 200


def _paging_fixture(bad: str | None) -> SortCursor:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    rows, loaded = [], {}
    for index in range(10):
        moment = start + timedelta(days=index)
        url = UNSAFE_URL if f"{index:064x}" == bad else None
        published = None if index == 3 else moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        row = _row(f"{index:064x}", published, "not_provided" if index == 3 else "present", url=url)
        rows.append(row)
        loaded[row[0]] = moment + timedelta(hours=1)
    return SortCursor(rows, loaded, columns=BOTH)


def _paged(monkeypatch: pytest.MonkeyPatch, cursor: SortCursor, size: int) -> list[Any]:
    _bind(monkeypatch, cursor)
    client = _client(SnowflakeFeedRepository(SETTINGS))
    pages, token = [], None
    while True:
        params: dict[str, Any] = {"page_size": size}
        if token is not None:
            params["page_token"] = token
        response = _feed(client, **params)
        assert response.status_code == 200, response.text
        assert UNSAFE_URL not in response.text
        pages.append(response.json())
        token = response.json()["meta"]["next_page_token"]
        if token is None:
            return pages


def test_sorted_paging_skips_a_bad_row_without_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sorted newest first: 9, 8, 7, 6, 5, 4, 3 (undated, at its load), 2, 1, 0.
    order = [f"{index:064x}" for index in (9, 8, 7, 6, 5, 4, 3, 2, 1, 0)]
    bad = order[1]  # mid-page on page 1
    with skip_log() as log:
        pages = _paged(monkeypatch, _paging_fixture(bad), 4)
    served = [[entry["item_id"] for entry in page["data"]] for page in pages]
    assert [len(page) for page in served] == [3, 4, 2]
    flattened = [item for page in served for item in page]
    assert flattened == [item for item in order if item != bad]
    assert len(set(flattened)) == len(flattened)
    assert [r.context["item_id"] for r in log.records if r.getMessage() == ROW_SKIPPED] == [bad]
    assert UNSAFE_URL not in log.text
    # Tokens come from the rows read, so they match a clean read of the same rows.
    clean = _paged(monkeypatch, _paging_fixture(None), 4)
    assert [page["meta"]["next_page_token"] for page in pages] == [
        page["meta"]["next_page_token"] for page in clean
    ]
    assert [entry["item_id"] for page in clean for entry in page["data"]] == order


@pytest.mark.parametrize(("size", "ok"), [(1, True), (500, True), (0, False), (501, False)])
def test_page_size_bounds_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, size: int, ok: bool
) -> None:
    cursor = _paging_fixture(None)
    _bind(monkeypatch, cursor)
    response = _feed(_client(SnowflakeFeedRepository(SETTINGS)), page_size=size)
    if ok:
        assert response.status_code == 200
        assert len(response.json()["data"]) == min(size, 10)
        assert cursor.page_sql()[-1].endswith(f"{FEED_SORT_AT_ORDER} LIMIT %s OFFSET %s")
    else:
        assert response.status_code in {400, 422}
        assert cursor.page_sql() == []


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (None, frozenset()),
        ((None,), frozenset()),
        (("",), frozenset()),
        (("FEED_SORT_AT",), SORT_ONLY),
        (("ABSTRACT_SUPPRESSED,FEED_SORT_AT,OTHER",), BOTH),
    ],
)
def test_lookup_reads_one_row_for_both_columns(
    answer: tuple[Any, ...] | None, expected: frozenset[str]
) -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def execute(self, sql: str, params: tuple[Any, ...], *, timeout: int) -> None:
            self.calls.append((sql, params))

        def fetchone(self) -> tuple[Any, ...] | None:
            return answer

    cursor = Cursor()
    repository = SnowflakeFeedRepository(SETTINGS)
    assert repository._optional_feed_columns(cursor) == expected
    [(sql, params)] = cursor.calls
    assert "ONE_HEALTH_LYME_GAP_ATLAS_PROD" in sql and "INFORMATION_SCHEMA.COLUMNS" in sql
    assert "TABLE_NAME = 'INTELLIGENCE_FEED_V2'" in sql
    assert "COLUMN_NAME IN (%s, %s)" in sql
    assert params == ("PRESENTATION", "ABSTRACT_SUPPRESSED", "FEED_SORT_AT")
    assert repository._abstract_suppressed_column_present(cursor) == (
        "ABSTRACT_SUPPRESSED" in expected
    )
    assert len(cursor.calls) == 1
