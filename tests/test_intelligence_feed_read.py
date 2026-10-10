"""Independent read-boundary, pagination and data semantics acceptance."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import ITEM_COLUMNS, V2_COLUMNS, SnowflakeFeedRepository
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


def test_enabled_empty_items_and_sources_are_honest() -> None:
    class EmptyRepository(Repository):
        def read(self, *args: Any) -> tuple[str, list[tuple[Any, ...]]]:
            self.calls.append(args)
            return "empty-projection", []

    repository = EmptyRepository()
    app = client(repository)
    for path in ("/v1/intelligence/items", "/v1/intelligence/sources"):
        response = app.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["next_page_token"] is None
        assert body["meta"]["source_health_available"] is False
        if path.endswith("/sources"):
            assert body["source_catalog_complete"] is False
    assert [call[0] for call in repository.calls] == ["items", "sources"]


def test_items_preserve_dates_and_provenance() -> None:
    repository = Repository()
    response = client(repository).get("/v1/intelligence/items")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    item = response.json()["data"][0]["item"]
    assert item == repository.document
    assert item["published_at"] != item["fetched_at"]
    assert response.json()["meta"]["source_health_available"] is False
    assert "raw" not in item


def test_two_sources_and_revisions_survive_http_paging_without_private_fields() -> None:
    class CrossSourceRepository(Repository):
        def read(self, kind: str, *args: Any) -> tuple[str, list[tuple[Any, ...]]]:
            self.calls.append((kind, *args))
            if kind == "sources":
                return "stable", [
                    ("cdc-newsroom", 1, "CDC", "official_public_health"),
                    ("nih-news-releases", 2, "NIH", "official_public_health"),
                ]
            first = deepcopy(self.document)
            first["source_id"] = "cdc-newsroom"
            revised = deepcopy(first)
            revised["revision_id"] = "b" * 64
            other = deepcopy(first)
            other.update(
                source_id="nih-news-releases",
                registry_version=2,
                item_id="c" * 64,
                revision_id="d" * 64,
                deduplication_key="c" * 64,
            )
            rows = [
                tuple(item[name] for name in ITEM_COLUMNS)
                + (organization, "official_public_health")
                for item, organization in ((first, "CDC"), (revised, "CDC"), (other, "NIH"))
            ]
            offset, size = args[-2:]
            return "stable", rows[offset : offset + size + 1]

    repository = CrossSourceRepository()
    app = client(repository)
    first_page = app.get("/v1/intelligence/items?page_size=2")
    assert first_page.status_code == 200
    assert first_page.headers["cache-control"] == "no-store"
    token = first_page.json()["meta"]["next_page_token"]
    assert token
    second_page = app.get(
        "/v1/intelligence/items", params={"page_size": 2, "page_token": token}
    )
    assert second_page.status_code == 200
    assert second_page.json()["meta"]["next_page_token"] is None
    evidence = first_page.json()["data"] + second_page.json()["data"]
    assert [(entry["item"]["source_id"], entry["item"]["revision_id"]) for entry in evidence] == [
        ("cdc-newsroom", repository.document["revision_id"]),
        ("cdc-newsroom", "b" * 64),
        ("nih-news-releases", "d" * 64),
    ]
    assert evidence[0]["item"]["item_id"] == evidence[1]["item"]["item_id"]
    assert evidence[2]["item"]["item_id"] != evidence[0]["item"]["item_id"]
    assert [entry["source"]["organization"] for entry in evidence] == ["CDC", "CDC", "NIH"]
    assert len({entry["item"]["canonical_url"] for entry in evidence}) == 1
    assert all("raw_publisher_date" not in entry["item"] for entry in evidence)
    assert all(
        entry["item"]["provenance"] == repository.document["provenance"] for entry in evidence
    )
    sources = app.get("/v1/intelligence/sources")
    assert sources.status_code == 200
    assert [(item["source_id"], item["registry_version"]) for item in sources.json()["data"]] == [
        ("cdc-newsroom", 1),
        ("nih-news-releases", 2),
    ]
    assert all(item["operational_state"] == "unavailable" for item in sources.json()["data"])


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


def test_v2_publisher_projection_without_raw_or_private_native_metadata() -> None:
    repository = Repository()
    document = repository.document
    document.update(
        contract_version="2.0.0",
        publisher_metadata={
            "publisher": "CDC",
            "source_family": "cdc_eid",
            "source_item_id": "publisher-guid-1",
            "authors": ["Publisher Author"],
            "categories": ["Tick borne research"],
            "language": "en",
            "media": [{"url": "https://www.cdc.gov/image.png", "origin": "publisher"}],
            "date_states": {
                key: document["field_states"][key] for key in ("published_at", "updated_at")
            },
        },
        derived_metadata={},
    )
    document["provenance"].update(
        parser_version="rss-atom-native-v2", normalization_version="intelligence-identity-v2"
    )

    class V2Repository(Repository):
        def read(self, *args: Any) -> tuple[str, list[tuple[Any, ...]]]:
            return "v2", [
                tuple(document[name] for name in V2_COLUMNS) + ("CDC", "official_public_health")
            ]

    app = client(V2Repository())
    response = app.get("/v1/intelligence/items")
    assert response.status_code == 200
    item = response.json()["data"][0]["item"]
    assert item["contract_version"] == "2.0.0"
    assert item["publisher_metadata"]["categories"] == ["Tick borne research"]
    assert item["derived_metadata"] == {}
    assert "native_metadata" not in item and "xml_base64" not in item
    for private_field in ("native_metadata", "xml_base64", "raw_publisher_date", "mailbox"):
        document["publisher_metadata"][private_field] = "private-source-value"
        rejected = app.get("/v1/intelligence/items")
        assert rejected.status_code == 503 and "private-source-value" not in rejected.text
        del document["publisher_metadata"][private_field]
    document["derived_metadata"] = {"urgency": "high"}
    assert app.get("/v1/intelligence/items").status_code == 503
    document["derived_metadata"] = {}
    for unsafe_url in (
        "https://127.0.0.1/private",
        "https://example.org/image?token=private-token",
        "https://localhost./x",
        "https://foo.localhost/x",
        "https://deep.foo.LOCALHOST/x",
        "https://127.1/x",
        "https://2130706433/x",
        "https://0x7f000001/x",
        "https://0177.0.0.1/x",
        "https://example.org/image?X-Amz-Signature=private-signature",
        "https://example.org/image?sig=private-signature",
    ):
        document["publisher_metadata"]["media"][0]["url"] = unsafe_url
        rejected = app.get("/v1/intelligence/items")
        assert rejected.status_code == 503 and unsafe_url not in rejected.text


@pytest.mark.parametrize("kind", ["items", "feed"])
@pytest.mark.parametrize("mode", ["stable", "changed", "too_broad", "driver_error"])
def test_sql_boundary_limits_and_state_checks(
    monkeypatch: pytest.MonkeyPatch, mode: str, kind: str
) -> None:
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
        marker, rows = repository.read(kind, "source'bound", None, None, 0, 10)
        assert len(marker) == 64 and rows == []
        assert cursor.calls[1][1] == ("source'bound", 11, 0)
        assert all("source'bound" not in sql for sql, _, _ in cursor.calls)
        if kind == "feed":
            assert "INTELLIGENCE_FEED_V2" in cursor.calls[1][0]
            selected = cursor.calls[1][0].split(" FROM ")[0]
            assert not any(field in selected for field in ("EXCERPT", "MEDIA", "NATIVE_METADATA"))
        assert all(
            "PRESENTATION.INTELLIGENCE_FEED_V" in sql.replace(chr(34), "")
            for sql, _, _ in cursor.calls
        )
    elif mode == "too_broad":
        with pytest.raises(PublicQueryError):
            repository.read(kind, None, None, None, 0, 10)
        assert len(cursor.calls) == 1
    else:
        with pytest.raises(AtlasDataUnavailableError) as error:
            repository.read(kind, None, None, None, 0, 10)
        assert "secret" not in str(error.value)
