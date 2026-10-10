"""Metadata-only V2 HTTP and SQL boundary tests."""

import json
from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_intelligence_feed_read import Repository

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import FEED_COLUMNS


class MetadataRepository(Repository):
    def read(
        self, kind: str, source: str | None, start: Any, end: Any, offset: int, size: int
    ) -> tuple[str, list[tuple[Any, ...]]]:
        self.calls.append((kind, source, start, end, offset, size))
        assert kind == "feed"
        document = deepcopy(self.document)
        document.update(
            contract_version="2.0.0",
            publisher="CDC",
            publication_date_state=document["field_states"]["published_at"],
        )
        document["provenance"].update(
            parser_version="rss-atom-native-v2", normalization_version="intelligence-identity-v2"
        )
        rows = [
            tuple(document[key] for key in FEED_COLUMNS)
            + ("cdc-eid-expedited", 1, "CDC", "official_public_health")
        ]
        return self.marker, rows[offset : offset + size + 1]


def client(repository: Repository, enabled: bool = True) -> TestClient:
    return TestClient(
        create_app(
            settings=ApiSettings(intelligence_feed_read_enabled=enabled),
            intelligence_repository=repository,
        )
    )


def test_metadata_only_and_unknown_publication_date() -> None:
    repository = MetadataRepository()
    repository.document["published_at"] = None
    repository.document["field_states"]["published_at"] = "not_provided"
    response = client(repository).get("/v1/intelligence/feed?source_id=cdc-eid-expedited")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    entry = response.json()["data"][0]
    assert set(entry) == set(FEED_COLUMNS) | {"source"}
    assert entry["published_at"] is None
    assert entry["publication_date_state"] == "not_provided"
    assert entry["source"]["source_id"] == "cdc-eid-expedited"
    assert entry["publisher"] == "CDC"
    assert entry["title"] == repository.document["title"]
    assert entry["canonical_url"] == repository.document["canonical_url"]
    assert entry["fetched_at"] == repository.document["fetched_at"]
    assert (
        entry["provenance"]["artifact_sha256"]
        == repository.document["provenance"]["artifact_sha256"]
    )
    assert not ({"excerpt", "description", "body", "images", "publisher_metadata"} & set(entry))
    assert repository.calls[0][1] == "cdc-eid-expedited"


def test_empty_disabled_and_unavailable() -> None:
    repository = MetadataRepository()
    response = client(repository, enabled=False).get("/v1/intelligence/feed")
    assert response.status_code == 503 and not repository.calls
    response = client(repository).get("/v1/intelligence/feed?page_size=1&page_token=invalid")
    assert response.status_code == 400

    class Empty(MetadataRepository):
        def read(self, *args: Any) -> tuple[str, list[tuple[Any, ...]]]:
            return "empty", []

    response = client(Empty()).get("/v1/intelligence/feed")
    assert response.status_code == 200
    assert response.json()["data"] == []
    assert response.json()["meta"]["next_page_token"] is None

    repository.document["provenance"]["private"] = "private-token"
    response = client(repository).get("/v1/intelligence/feed")
    assert response.status_code == 503 and "private-token" not in response.text


@pytest.mark.parametrize(
    "query",
    [
        "page_size=0",
        "page_size=501",
        "source_id=bad%27value",
        "unknown=1",
        "page_size=1&page_size=2",
        "published_from=2026-10-10&published_to=2026-10-01",
    ],
)
def test_feed_bounds(query: str) -> None:
    assert client(MetadataRepository()).get("/v1/intelligence/feed?" + query).status_code in {
        400,
        422,
    }


def test_openapi_excludes_content_and_public_analytics() -> None:
    app = create_app(settings=ApiSettings())
    contract = app.first_party_openapi()
    assert "/v1/intelligence/feed" in contract["paths"]
    assert "/v1/intelligence/feed" not in app.openapi()["paths"]
    properties = contract["components"]["schemas"]["FeedEntry"]["properties"]
    assert set(properties) == set(FEED_COLUMNS) | {"source"}
    assert "excerpt" not in json.dumps(properties)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.org",
        "https://u:p@example.org",
        "https://example.org/#fragment",
        "https://example.org:444/",
        "https://example.org/a b",
        "https://example.org:bad/",
    ],
)
def test_unsafe_canonical_links_fail_closed(url: str) -> None:
    repository = MetadataRepository()
    repository.document["canonical_url"] = url
    response = client(repository).get("/v1/intelligence/feed")
    assert response.status_code == 503
    assert url not in response.text
