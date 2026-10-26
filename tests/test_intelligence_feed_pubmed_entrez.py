"""Regression: PubMed rows from the Entrez (efetch) parser must project on every feed route.

The first PROD PubMed run carried provenance.parser_version "entrez-efetch-v1". The
projection accepted only "rss-atom-native-v2", so one PubMed row made the whole
/v1/intelligence/feed list fail closed with 503.
"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_intelligence_feed_read import Repository

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import FeedService, _pack_feed_row
from lyme_gap_atlas_api.intelligence_projection import ProjectionProvenance

_FIXTURE = Path(__file__).parent / "fixtures" / "pubmed_entrez_feed_rows.json"
_COLUMNS = (
    "item_id",
    "revision_id",
    "title",
    "canonical_url",
    "published_at",
    "fetched_at",
    "provenance",
    "limitations",
    "contract_version",
    "publication_date_state",
    "publisher",
    "publisher_feed",
    "source_id",
    "registry_version",
    "organization",
    "reviewed_trust_classification",
)


def _documents() -> list[dict[str, Any]]:
    return deepcopy(json.loads(_FIXTURE.read_text())["rows"])


def _row(document: dict[str, Any]) -> tuple[Any, ...]:
    # Snowflake returns VARIANT columns as JSON text; keep that shape.
    raw = tuple(
        json.dumps(document[name])
        if name in {"provenance", "limitations", "publisher_feed"}
        else document[name]
        for name in _COLUMNS
    )
    return _pack_feed_row(raw, False)


class PubMedRepository(Repository):
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        super().__init__()
        self.documents = documents

    def read(
        self, kind: str, source: str | None, start: Any, end: Any, offset: int, size: int
    ) -> tuple[str, list[tuple[Any, ...]]]:
        assert kind == "feed"
        rows = [_row(document) for document in self.documents]
        return "pubmed", rows[offset : offset + size + 1]

    def read_item(self, item_id: str) -> list[tuple[Any, ...]]:
        return [_row(d) for d in self.documents if d["item_id"] == item_id]


def _client(repository: Repository) -> TestClient:
    return TestClient(
        create_app(
            settings=ApiSettings(intelligence_feed_read_enabled=True),
            intelligence_repository=repository,
        )
    )


def test_fixture_matches_the_prod_pubmed_shape() -> None:
    documents = _documents()
    assert {d["provenance"]["parser_version"] for d in documents} == {"entrez-efetch-v1"}
    assert {d["reviewed_trust_classification"] for d in documents} == {"research_index"}
    assert {d["publisher_feed"]["source_family"] for d in documents} == {"pubmed"}
    assert [d["published_at"] for d in documents] == [None, "2027-01-01T00:00:00Z"]


def test_pubmed_entrez_rows_list_and_entry_return_200() -> None:
    documents = _documents()
    app = _client(PubMedRepository(documents))
    response = app.get("/v1/intelligence/feed?page_size=25")
    assert response.status_code == 200, response.text
    entries = response.json()["data"]
    assert len(entries) == 2
    by_id = {entry["item_id"]: entry for entry in entries}
    for document in documents:
        entry = by_id[document["item_id"]]
        pmid = document["publisher_feed"]["source_item_id"]
        assert entry["provenance"]["parser_version"] == "entrez-efetch-v1"
        assert entry["source_family"] == "pubmed"
        assert entry["source_family_label"] == "PubMed"
        assert entry["pmid"] == pmid
        assert entry["pubmed_url"] == f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        assert entry["abstract_state"] == "withheld"
        assert entry["abstract_preview"] is None
        assert entry["abstract_has_more"] is False
        assert entry["published_at"] == document["published_at"]
        assert entry["source"]["reviewed_trust_classification"] == "research_index"
        detail = app.get(f"/v1/intelligence/feed/{document['item_id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["pubmed_url"] == entry["pubmed_url"]


def test_projection_validates_entrez_rows_without_http() -> None:
    rows = [_row(document) for document in _documents()]
    entries = FeedService(PubMedRepository(_documents()))._feed_entries(rows)
    assert [entry.source_family for entry in entries] == ["pubmed", "pubmed"]


@pytest.mark.parametrize("parser_version", ["rss-atom-native-v2", "entrez-efetch-v1"])
def test_parser_version_allowlist_accepts_known_parsers(parser_version: str) -> None:
    provenance = dict(_documents()[0]["provenance"], parser_version=parser_version)
    assert ProjectionProvenance.model_validate(provenance).parser_version == parser_version


@pytest.mark.parametrize("parser_version", ["entrez-efetch-v2", "rss-atom-native-v1", "", "x"])
def test_parser_version_allowlist_rejects_other_values(parser_version: str) -> None:
    provenance = dict(_documents()[0]["provenance"], parser_version=parser_version)
    with pytest.raises(ValidationError):
        ProjectionProvenance.model_validate(provenance)


def test_unknown_parser_version_still_fails_closed_without_disclosure() -> None:
    documents = _documents()
    documents[0]["provenance"]["parser_version"] = "private-parser-value"
    response = _client(PubMedRepository(documents)).get("/v1/intelligence/feed")
    assert response.status_code == 503
    assert "private-parser-value" not in response.text
