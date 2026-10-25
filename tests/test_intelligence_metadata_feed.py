"""Metadata-only V2 HTTP and SQL boundary tests."""

import json
from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_intelligence_feed_read import Repository

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import (
    _SOURCE_FAMILIES,
    _SOURCE_FAMILY_LABELS,
    ABSTRACT_PREVIEW_MAX_LENGTH,
    ABSTRACT_TEXT_MAX_LENGTH,
    FEED_COLUMNS,
    FEED_ENTRY_COLUMNS,
    FeedListEntry,
    _pack_feed_row,
)
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError

_CDC_ATTRIBUTION = "Source: Centers for Disease Control and Prevention"
_PUBMED_ATTRIBUTION = "Courtesy of the U.S. National Library of Medicine"


def _publisher_feed(document: dict[str, Any]) -> dict[str, Any]:
    feed = document.get("publisher_feed", document.get("publisher_metadata"))
    if isinstance(feed, dict):
        return feed
    return {
        "source_family": "cdc_eid",
        "description": None,
        "categories": None,
        "authors": None,
        "abstract": None,
        "source_item_id": None,
    }


def feed_row(
    document: dict[str, Any],
    source: tuple[Any, ...] = ("cdc-eid-expedited", 1, "CDC", "official_public_health"),
) -> tuple[Any, ...]:
    return (
        document["item_id"],
        document["revision_id"],
        document["title"],
        document["canonical_url"],
        document["published_at"],
        document["fetched_at"],
        document["provenance"],
        document["limitations"],
        document["contract_version"],
        document["publication_date_state"],
        document["publisher"],
        _publisher_feed(document),
        *source,
    )


class MetadataRepository(Repository):
    def __init__(self) -> None:
        super().__init__()
        self.item_calls: list[str] = []

    def _documents(self) -> list[dict[str, Any]]:
        document = deepcopy(self.document)
        document.update(
            contract_version="2.0.0",
            publisher=document.get("publisher", "CDC"),
            publication_date_state=document["field_states"]["published_at"],
        )
        document["provenance"].update(
            parser_version="rss-atom-native-v2", normalization_version="intelligence-identity-v2"
        )
        return [document]

    def read(
        self, kind: str, source: str | None, start: Any, end: Any, offset: int, size: int
    ) -> tuple[str, list[tuple[Any, ...]]]:
        self.calls.append((kind, source, start, end, offset, size))
        assert kind == "feed"
        rows = [feed_row(document) for document in self._documents()]
        return self.marker, rows[offset : offset + size + 1]

    def read_item(self, item_id: str) -> list[tuple[Any, ...]]:
        self.item_calls.append(item_id)
        return [row for row in self.read("feed", None, None, None, 0, 1)[1] if row[0] == item_id]


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
    assert not ({"excerpt", "body", "images", "publisher_metadata"} & set(entry))
    assert entry["source_family"] == "cdc_eid"
    assert entry["description"] == {"text": None, "state": "withheld"}
    assert "abstract" not in entry
    assert entry["abstract_preview"] is None
    assert entry["abstract_has_more"] is False
    assert entry["abstract_state"] is None
    assert entry["copyright_line"] is None
    assert entry["pubmed_url"] is None
    assert entry["tags"] == []
    assert entry["authors"] == []
    assert entry["pmid"] is None
    assert entry["attribution"] == _CDC_ATTRIBUTION
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
    entry_path = "/v1/intelligence/feed/{item_id}"
    assert entry_path in contract["paths"]
    assert entry_path not in app.openapi()["paths"]
    operation = contract["paths"]["/v1/intelligence/feed"]["get"]
    entry_operation = contract["paths"][entry_path]["get"]
    assert entry_operation["operationId"] == "getIntelligenceFeedEntry"
    for text in (operation["description"], entry_operation["description"]):
        assert "No excerpts, article bodies or media." not in text
        assert "publisher summaries" in text
        assert "abstracts with their copyright line" in text
        assert "article bodies" in text
    assert (
        entry_operation["responses"]["404"]["content"]["application/problem+json"]["examples"][
            "intelligence_item_not_found"
        ]["value"]["code"]
        == "INTELLIGENCE_ITEM_NOT_FOUND"
    )
    list_properties = contract["components"]["schemas"]["FeedListEntry"]["properties"]
    entry_properties = contract["components"]["schemas"]["FeedEntry"]["properties"]
    assert set(list_properties) == set(FEED_COLUMNS) | {"source"}
    assert set(entry_properties) == set(FEED_ENTRY_COLUMNS) | {"source"}
    assert list_properties["source_family_label"]["description"] == (
        "Display label for source_family."
    )
    assert "excerpt" not in json.dumps(list_properties)
    assert "abstract" not in list_properties
    assert list_properties["description"]["$ref"].endswith("/FeedDescription")
    assert "FeedAbstract" in json.dumps(entry_properties["abstract"])
    abstract_text = contract["components"]["schemas"]["FeedAbstract"]["properties"]["text"]
    assert abstract_text["maxLength"] == ABSTRACT_TEXT_MAX_LENGTH
    assert str(ABSTRACT_TEXT_MAX_LENGTH) in abstract_text["description"]
    assert str(ABSTRACT_TEXT_MAX_LENGTH) in operation["description"]
    assert str(ABSTRACT_PREVIEW_MAX_LENGTH) in operation["description"]
    assert "full abstract text" in operation["description"]
    assert str(ABSTRACT_TEXT_MAX_LENGTH) in entry_operation["description"]
    assert "abstract.text" in entry_operation["description"]


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


def _feed(**overrides: Any) -> dict[str, Any]:
    document = {
        "source_family": "cdc_mmwr",
        "description": {
            "text": "X",
            "state": "present",
            "format": "plain_text",
            "origin": "publisher",
        },
        "categories": [],
        "authors": [],
        "abstract": None,
        "source_item_id": None,
    }
    document.update(overrides)
    return document


def _entry(feed: dict[str, Any], **document: Any) -> dict[str, Any]:
    repository = MetadataRepository()
    repository.document["publisher_metadata"] = feed
    repository.document.update(document)
    response = client(repository).get("/v1/intelligence/feed?page_size=1")
    assert response.status_code == 200, response.text
    return response.json()["data"][0]


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            {"text": "X", "state": "present", "format": "plain_text", "origin": "publisher"},
            {"text": "X", "state": "present"},
        ),
        (
            {"text": "", "state": "empty", "format": "plain_text", "origin": "publisher"},
            {"text": "", "state": "empty"},
        ),
        (
            {"text": None, "state": "not_provided", "format": "plain_text", "origin": "publisher"},
            {"text": None, "state": "not_provided"},
        ),
        (
            {"text": None, "state": "withheld", "format": "plain_text", "origin": "publisher"},
            {"text": None, "state": "withheld"},
        ),
        (None, {"text": None, "state": "withheld"}),
    ],
)
def test_description_states(description: Any, expected: dict[str, Any]) -> None:
    entry = _entry(_feed(description=description))
    assert entry["description"] == expected
    assert "abstract" not in entry
    assert entry["abstract_state"] is None
    assert entry["pmid"] is None
    assert entry["source_family"] == "cdc_mmwr"
    assert entry["attribution"] == _CDC_ATTRIBUTION


def test_legacy_row_withholds_description_and_returns_empty_labels() -> None:
    entry = _entry(
        {
            "source_family": "cdc_mmwr",
            "source_item_id": "mmwr-1",
        }
    )
    assert entry["description"] == {"text": None, "state": "withheld"}
    assert entry["tags"] == []
    assert entry["authors"] == []
    assert entry["abstract_state"] is None
    assert "abstract" not in entry


def test_invalid_description_withholds_one_entry_and_logs_a_count(
    capfd: pytest.CaptureFixture[str],
) -> None:

    class Page(MetadataRepository):
        def _documents(self) -> list[dict[str, Any]]:
            bad, good = super()._documents()[0], super()._documents()[0]
            bad["item_id"] = "a" * 64
            bad["publisher_metadata"] = _feed(
                description={"text": "<script>bad()</script>", "state": "present"}
            )
            good["item_id"] = "b" * 64
            good["publisher_metadata"] = _feed()
            return [bad, good]

    response = client(Page()).get("/v1/intelligence/feed?page_size=2")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body[0]["description"] == {"text": None, "state": "withheld"}
    assert body[1]["description"] == {"text": "X", "state": "present"}
    assert "<script>" not in response.text
    captured = capfd.readouterr()
    assert "<script>" not in captured.err
    assert "withheld_text_count=1" in captured.err


@pytest.mark.parametrize(
    "description",
    [
        {"text": "has > bracket", "state": "present"},
        {"text": "line\nbreak", "state": "present"},
        {"text": "x" * 4097, "state": "present"},
        {"text": None, "state": "empty"},
        {"text": "unexpected", "state": "not_provided"},
        {"text": None, "state": "present"},
        {"text": "X", "state": "present", "format": "html", "origin": "publisher"},
    ],
)
def test_invalid_or_inconsistent_description_is_withheld(description: dict[str, Any]) -> None:
    entry = _entry(_feed(description=description))
    assert entry["description"] == {"text": None, "state": "withheld"}


def _listed_and_detail(
    feed: dict[str, Any], **document: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    repository = MetadataRepository()
    repository.document["publisher_metadata"] = feed
    repository.document.update(document)
    http = client(repository)
    listed_response = http.get("/v1/intelligence/feed?page_size=1")
    assert listed_response.status_code == 200, listed_response.text
    listed = listed_response.json()["data"][0]
    detail_response = http.get(f"/v1/intelligence/feed/{listed['item_id']}")
    assert detail_response.status_code == 200, detail_response.text
    return listed, detail_response.json()["data"]


def test_pubmed_abstract_is_not_copied_into_description() -> None:
    pmid = "41234567"
    abstract = "We collected 1,204 host-seeking nymphs."
    copyright_line = "© 2026 Elsevier GmbH."
    listed, detail = _listed_and_detail(
        _feed(
            source_family="pubmed",
            description={
                "text": abstract,
                "state": "present",
                "format": "plain_text",
                "origin": "publisher",
            },
            abstract={
                "text": abstract,
                "state": "present",
                "copyright_line": copyright_line,
            },
            categories=["Lyme disease", "Ixodes scapularis"],
            authors=["Smith J", "Lee K"],
            source_item_id=pmid,
        ),
        canonical_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        publisher="Ticks and Tick-borne Diseases",
    )
    assert listed["description"] == {"text": None, "state": "not_provided"}
    assert "abstract" not in listed
    assert listed["abstract_preview"] == abstract
    assert listed["abstract_has_more"] is False
    assert listed["abstract_state"] == "present"
    assert listed["copyright_line"] == copyright_line
    assert detail["abstract"] == {
        "text": abstract,
        "state": "present",
        "copyright_line": copyright_line,
    }
    without_preview = {key: value for key, value in listed.items() if key != "abstract_preview"}
    assert abstract not in json.dumps(without_preview)
    for entry in (listed, detail):
        assert entry["pmid"] == pmid
        assert entry["pubmed_url"] == f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        assert entry["canonical_url"] == f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        assert entry["attribution"] == _PUBMED_ATTRIBUTION
        assert entry["tags"] == ["Lyme disease", "Ixodes scapularis"]
        assert entry["authors"] == ["Smith J", "Lee K"]


@pytest.mark.parametrize(
    ("abstract", "expected"),
    [
        (None, {"text": None, "state": "withheld", "copyright_line": None}),
        (
            {"text": "", "state": "empty"},
            {"text": "", "state": "empty", "copyright_line": None},
        ),
        (
            {"text": None, "state": "not_provided"},
            {"text": None, "state": "not_provided", "copyright_line": None},
        ),
        (
            {"text": None, "state": "withheld", "copyright_line": "© secret"},
            {"text": None, "state": "withheld", "copyright_line": None},
        ),
        (
            {"text": "<script>", "state": "present", "copyright_line": "© 2026"},
            {"text": None, "state": "withheld", "copyright_line": None},
        ),
        (
            {"text": "y" * (ABSTRACT_TEXT_MAX_LENGTH + 1), "state": "present"},
            {"text": None, "state": "withheld", "copyright_line": None},
        ),
        (
            {"text": "Results", "state": "present", "copyright_line": "<b>©</b>"},
            {"text": "Results", "state": "present", "copyright_line": None},
        ),
        (
            {"text": "Results", "state": "suppressed", "copyright_line": "© 2026"},
            {"text": None, "state": "suppressed", "copyright_line": None},
        ),
    ],
)
def test_pubmed_abstract_states(abstract: Any, expected: dict[str, Any]) -> None:
    listed, detail = _listed_and_detail(
        _feed(
            source_family="pubmed",
            abstract=abstract,
            source_item_id="41234567",
            description={"text": "not the abstract", "state": "present"},
        )
    )
    assert detail["abstract"] == expected
    assert detail["description"] == {"text": None, "state": "not_provided"}
    assert "abstract" not in listed
    assert listed["abstract_state"] == expected["state"]
    assert listed["description"] == {"text": None, "state": "not_provided"}
    if expected["state"] == "present":
        assert listed["abstract_preview"] == expected["text"]
        assert listed["abstract_has_more"] is False
        assert listed["copyright_line"] == expected["copyright_line"]
    else:
        assert listed["abstract_preview"] is None
        assert listed["abstract_has_more"] is False
        assert listed["copyright_line"] is None


def test_suppression_hides_abstract_text_and_leaves_other_fields() -> None:
    shown, shown_detail = _listed_and_detail(
        _feed(
            source_family="pubmed",
            abstract={"text": "Visible abstract", "state": "present", "copyright_line": "© 2026"},
            categories=["Lyme disease"],
            authors=["Smith J"],
            source_item_id="41234567",
        ),
        title="Borrelia study",
    )
    hidden, hidden_detail = _listed_and_detail(
        _feed(
            source_family="pubmed",
            abstract_suppressed=True,
            abstract={"text": "Visible abstract", "state": "present", "copyright_line": "© 2026"},
            categories=["Lyme disease"],
            authors=["Smith J"],
            source_item_id="41234567",
        ),
        title="Borrelia study",
    )
    assert hidden["abstract_state"] == "suppressed"
    assert hidden["abstract_preview"] is None
    assert hidden["abstract_has_more"] is False
    assert hidden["copyright_line"] is None
    assert hidden_detail["abstract"] == {
        "text": None,
        "state": "suppressed",
        "copyright_line": None,
    }
    assert "Visible abstract" not in json.dumps(hidden)
    assert "Visible abstract" not in json.dumps(hidden_detail)
    unchanged = (
        "title",
        "description",
        "tags",
        "authors",
        "pmid",
        "pubmed_url",
        "attribution",
        "canonical_url",
    )
    for field in unchanged:
        assert hidden[field] == shown[field]
        assert hidden_detail[field] == shown_detail[field]


def test_non_pubmed_abstract_stays_null_when_a_stored_abstract_or_flag_is_present() -> None:
    entry = _entry(
        _feed(
            abstract={"text": "Not a PubMed abstract", "state": "present"},
            abstract_suppressed=True,
            description={"text": "Publisher summary", "state": "present"},
        )
    )
    assert "abstract" not in entry
    assert entry["abstract_preview"] is None
    assert entry["abstract_has_more"] is False
    assert entry["abstract_state"] is None
    assert entry["copyright_line"] is None
    assert entry["description"] == {"text": "Publisher summary", "state": "present"}
    assert entry["pmid"] is None
    assert entry["pubmed_url"] is None


def test_every_source_family_has_a_label() -> None:
    assert set(_SOURCE_FAMILY_LABELS) == set(_SOURCE_FAMILIES)
    assert all(isinstance(label, str) and label for label in _SOURCE_FAMILY_LABELS.values())
    entry = _entry(_feed())
    entry["source_family_label"] = "Other"
    with pytest.raises(ValidationError):
        FeedListEntry.model_validate(entry)


@pytest.mark.parametrize(
    ("family", "publisher", "attribution", "label"),
    [
        ("cdc_mmwr", "CDC", _CDC_ATTRIBUTION, "CDC MMWR"),
        ("cdc_eid", "CDC", _CDC_ATTRIBUTION, "CDC EID"),
        ("cdc_other", "CDC", _CDC_ATTRIBUTION, "CDC"),
        ("nih_niaid", "NIH", "Source: National Institutes of Health", "NIH NIAID"),
        ("pubmed", "Journal", _PUBMED_ATTRIBUTION, "PubMed"),
        ("state_local", "Maine CDC", "Source: Maine CDC", "State and local"),
        ("other", "ProMED", "Source: ProMED", "Other"),
    ],
)
def test_source_family_sets_attribution(
    family: str, publisher: str, attribution: str, label: str
) -> None:
    feed = _feed(source_family=family)
    if family == "pubmed":
        feed["source_item_id"] = "123"
        feed["abstract"] = {"text": "Abstract", "state": "present", "copyright_line": None}
    entry = _entry(feed, publisher=publisher)
    assert entry["source_family"] == family
    assert entry["source_family_label"] == label
    assert entry["attribution"] == attribution
    assert entry["publisher"] == publisher


def test_named_sources_keep_their_families() -> None:
    class Source(MetadataRepository):
        def read(
            self, kind: str, source: str | None, start: Any, end: Any, offset: int, size: int
        ) -> tuple[str, list[tuple[Any, ...]]]:
            document = self._documents()[0]
            return self.marker, [
                feed_row(document, (document["source_id"], 1, "Org", "official_public_health"))
            ]

    expected = {
        "cdc-eid-expedited": "cdc_eid",
        "cdc-outbreaks": "cdc_other",
        "nih-news-releases": "nih_niaid",
    }
    for source_id, family in expected.items():
        repository = Source()
        repository.document["source_id"] = source_id
        repository.document["publisher_metadata"] = _feed(source_family=family)
        entry = client(repository).get("/v1/intelligence/feed").json()["data"][0]
        assert entry["source"]["source_id"] == source_id
        assert entry["source_family"] == family


def test_tags_drop_mmwr_series_labels_and_duplicate_case() -> None:
    labels = [
        "MMWR",
        "mmwr",
        "Morbidity & Mortality Weekly Report",
        "Lyme disease",
        "lyme disease",
        "Ticks",
    ]
    entry = _entry(_feed(categories=labels + [f"tag-{index}" for index in range(120)]))
    assert entry["tags"][0] == "Lyme disease"
    assert entry["tags"][1] == "Ticks"
    assert "MMWR" not in entry["tags"]
    assert "Morbidity & Mortality Weekly Report" not in entry["tags"]
    assert len(entry["tags"]) == 100


def test_authors_come_from_structured_fields_only() -> None:
    entry = _entry(
        _feed(
            description={"text": "By Ada Lovelace", "state": "present"},
            authors=[
                "Ada Lovelace",
                "a" * 301,
                "<bad>",
                *[f"Author {index}" for index in range(25)],
            ],
        )
    )
    assert entry["description"]["text"] == "By Ada Lovelace"
    assert entry["authors"][0] == "Ada Lovelace"
    assert "<bad>" not in entry["authors"]
    assert all(len(author) <= 300 for author in entry["authors"])
    assert len(entry["authors"]) == 20


def test_entry_by_id_matches_the_list_and_unknown_ids_are_not_found() -> None:
    repository = MetadataRepository()
    repository.document["publisher_metadata"] = _feed()
    http = client(repository)
    listed = http.get("/v1/intelligence/feed").json()["data"][0]
    item_id = listed["item_id"]
    response = http.get(f"/v1/intelligence/feed/{item_id}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    detail = response.json()["data"]
    shared = set(listed) & set(detail)
    for field in shared:
        assert detail[field] == listed[field]
    assert "abstract" not in listed
    assert detail["abstract"] is None
    assert listed["abstract_state"] is None
    assert listed["source_family_label"] == "CDC MMWR"
    assert detail["source_family_label"] == "CDC MMWR"
    assert response.json()["meta"] == {"projection_version": "intelligence-feed-read-v2"}
    missing = http.get("/v1/intelligence/feed/" + "ab" * 32)
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.headers["cache-control"] == "no-store"
    assert missing.json()["code"] == "INTELLIGENCE_ITEM_NOT_FOUND"
    for malformed in ("abc", "g" * 64, "A" * 64):
        rejected = http.get(f"/v1/intelligence/feed/{malformed}")
        assert rejected.status_code == 422
        assert rejected.headers["content-type"].startswith("application/problem+json")


def test_entry_disabled_and_unavailable() -> None:
    repository = MetadataRepository()
    item_id = repository.document["item_id"]
    disabled = client(repository, enabled=False).get(f"/v1/intelligence/feed/{item_id}")
    assert disabled.status_code == 503
    assert repository.item_calls == []
    assert disabled.json()["code"] == "SERVICE_UNAVAILABLE"

    class Down(MetadataRepository):
        def read_item(self, item_id: str) -> list[tuple[Any, ...]]:
            raise AtlasDataUnavailableError("unavailable")

    unavailable = client(Down()).get(f"/v1/intelligence/feed/{item_id}")
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "SERVICE_UNAVAILABLE"


def test_optional_suppression_column_merges_without_dropping_the_abstract() -> None:
    prefix = tuple(f"value-{index}" for index in range(11))
    feed = {"source_family": "pubmed", "abstract": {"text": "A", "state": "present"}}
    packed = _pack_feed_row(
        (*prefix, feed, True, "pubmed-tick-borne", 1, "NLM", "research_index"),
        True,
    )
    assert packed[11]["abstract_suppressed"] is True
    assert packed[11]["abstract"]["text"] == "A"
    assert packed[-4:] == ("pubmed-tick-borne", 1, "NLM", "research_index")
    variant = _pack_feed_row(
        (*prefix, {"abstract_suppressed": True}, "cdc-mmwr", 1, "CDC", "official_public_health"),
        False,
    )
    assert variant[11]["abstract_suppressed"] is True
