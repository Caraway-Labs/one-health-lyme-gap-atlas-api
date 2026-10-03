"""Independent read-boundary, pagination and data semantics acceptance."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import ITEM_COLUMNS, SnowflakeFeedRepository
from lyme_gap_atlas_api.public_contract import PublicQueryError
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError

FIXTURE = Path(__file__).parent / "fixtures/intelligence/rss-item.json"


class Repository:
    def __init__(self) -> None:
        self.marker = "stable"
        self.calls: list[tuple[Any, ...]] = []
        self.document = json.loads(FIXTURE.read_text())
        self.fail = False

    def read(
        self, kind: str, source_id: str | None, start: Any, end: Any, offset: int, size: int
    ) -> tuple[str, list[tuple[Any, ...]]]:
        self.calls.append((kind, source_id, start, end, offset, size))
        if self.fail:
            raise AtlasDataUnavailableError("Governed intelligence data is unavailable.")
        item = self.document
        if kind == "sources":
            return self.marker, [
                (item["source_id"], item["registry_version"], "CDC", "official_public_health")
            ]
        values = tuple(item[name] for name in ITEM_COLUMNS) + ("CDC", "official_public_health")
        other = deepcopy(item)
        other["revision_id"] = "b" * 64
        rows = [
            values,
            tuple(other[name] for name in ITEM_COLUMNS) + ("CDC", "official_public_health"),
        ]
        return self.marker, rows[offset : offset + size + 1]


def client(repository: Repository, *, enabled: bool = True) -> TestClient:
    return TestClient(
        create_app(
            settings=ApiSettings(intelligence_feed_read_enabled=enabled),
            intelligence_repository=repository,
        )
    )


def test_disabled_has_no_query_and_does_not_claim_empty() -> None:
    repository = Repository()
    response = client(repository, enabled=False).get("/v1/intelligence/items")
    assert response.status_code == 503
    assert repository.calls == []
    assert response.headers["content-type"].startswith("application/problem+json")


def test_items_preserve_dates_provenance_and_cross_source_identity() -> None:
    repository = Repository()
    response = client(repository).get("/v1/intelligence/items")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    item = response.json()["data"][0]["item"]
    assert item == repository.document
    assert item["published_at"] != item["fetched_at"]
    assert response.json()["meta"]["source_health_available"] is False
    assert "raw" not in item


def test_missing_publisher_date_remains_unknown() -> None:
    repository = Repository()
    repository.document["published_at"] = None
    repository.document["field_states"]["published_at"] = "not_provided"
    response = client(repository).get("/v1/intelligence/items")
    assert response.status_code == 200
    assert response.json()["data"][0]["item"]["published_at"] is None


def test_page_tokens_bind_filters_and_projection_state() -> None:
    repository = Repository()
    app = client(repository)
    first = app.get("/v1/intelligence/items?page_size=1")
    token = first.json()["meta"]["next_page_token"]
    assert (
        app.get("/v1/intelligence/items", params={"page_size": 1, "page_token": token}).status_code
        == 200
    )
    assert repository.calls[-1][4] == 1
    assert (
        app.get(
            "/v1/intelligence/items",
            params={"page_size": 1, "page_token": token, "source_id": "other"},
        ).status_code
        == 400
    )
    repository.marker = "changed"
    assert (
        app.get("/v1/intelligence/items", params={"page_size": 1, "page_token": token}).status_code
        == 400
    )


def test_source_health_is_unknown_and_catalog_is_not_complete_registry() -> None:
    response = client(Repository()).get("/v1/intelligence/sources")
    assert response.status_code == 200
    assert response.json()["data"][0]["operational_state"] == "unavailable"
    assert response.json()["source_catalog_complete"] is False


def test_private_nested_fields_fail_closed_without_value_disclosure() -> None:
    repository = Repository()
    repository.document["provenance"]["mailbox"] = "private@example.org"
    response = client(repository).get("/v1/intelligence/items")
    assert response.status_code == 503
    assert "private@example.org" not in response.text


@pytest.mark.parametrize(
    "query", ["page_size=501", "page_size=0", "source_id=bad%27value", "page_size=1&page_size=2"]
)
def test_query_controls(query: str) -> None:
    response = client(Repository()).get("/v1/intelligence/items?" + query)
    assert response.status_code in {400, 422}


def test_first_party_export_only_and_existing_external_paths_unchanged() -> None:
    app = create_app(settings=ApiSettings())
    assert "/v1/intelligence/items" in app.first_party_openapi()["paths"]
    assert "/v1/intelligence/items" not in app.openapi()["paths"]


def test_legacy_database_rejected_before_connection() -> None:
    with pytest.raises(AtlasDataUnavailableError):
        SnowflakeFeedRepository(ApiSettings()).read("items", None, None, None, 0, 100)


@pytest.mark.parametrize("mode", ["stable", "changed", "too_broad", "driver_error"])
def test_sql_boundary_limits_and_state_checks(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []
            self.states = iter(
                [(10001 if mode == "too_broad" else 2, 123), (2, 456 if mode == "changed" else 123)]
            )

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execute(self, sql: str, params: tuple[Any, ...], *, timeout: int) -> None:
            self.calls.append((sql, params, timeout))
            if mode == "driver_error":
                raise RuntimeError("secret account and private SQL error")

        def fetchone(self) -> tuple[int, int]:
            return next(self.states)

        def fetchall(self) -> list[tuple[Any, ...]]:
            return []

    cursor = Cursor()

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def cursor(self) -> Cursor:
            return cursor

    monkeypatch.setattr(
        "lyme_gap_atlas_api.intelligence_feed.connect", lambda settings: Connection()
    )
    repository = SnowflakeFeedRepository(
        ApiSettings(snowflake_presentation_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD")
    )
    if mode == "stable":
        marker, rows = repository.read("items", "source'bound", None, None, 0, 10)
        assert len(marker) == 64 and rows == []
        assert cursor.calls[1][1] == ("source'bound", 11, 0)
        assert all("source'bound" not in sql for sql, _, _ in cursor.calls)
        assert all(
            "PRESENTATION.INTELLIGENCE_FEED_V" in sql.replace(chr(34), "")
            for sql, _, _ in cursor.calls
        )
    elif mode == "too_broad":
        with pytest.raises(PublicQueryError):
            repository.read("items", None, None, None, 0, 10)
        assert len(cursor.calls) == 1
    else:
        with pytest.raises(AtlasDataUnavailableError) as error:
            repository.read("items", None, None, None, 0, 10)
        assert "secret" not in str(error.value)
