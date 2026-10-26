"""Rows that cannot be projected safely are omitted from list pages, never rendered."""

import json
from copy import deepcopy
from typing import Any

import pytest
from _feed_skip import ROW_SKIPPED, ROWS_SKIPPED, assert_row_skipped, skip_log
from pydantic import ValidationError
from test_intelligence_feed_read import Repository
from test_intelligence_feed_read import client as items_client
from test_intelligence_metadata_feed import MetadataRepository, client

from lyme_gap_atlas_api.intelligence_feed import (
    ITEM_COLUMNS,
    VARIANTS,
    _failing_fields,
    _feed_row_identity,
    _item_row_identity,
    _safe_identifier,
)

UNSAFE_URL = "javascript:alert('private-link')"


def _identity(index: int) -> str:
    return format(index, "064x")


class PagedRepository(MetadataRepository):
    """Ten raw rows in store order; ``bad`` positions carry an unsafe canonical link."""

    def __init__(self, bad: tuple[int, ...]) -> None:
        super().__init__()
        self.bad = bad

    def _documents(self) -> list[dict[str, Any]]:
        template = super()._documents()[0]
        documents = []
        for index in range(10):
            document = deepcopy(template)
            document["item_id"] = _identity(index + 1)
            if index in self.bad:
                document["canonical_url"] = UNSAFE_URL
            documents.append(document)
        return documents


def _pages(repository: PagedRepository, page_size: int) -> list[dict[str, Any]]:
    app = client(repository)
    pages, token = [], None
    while True:
        params: dict[str, Any] = {"page_size": page_size}
        if token is not None:
            params["page_token"] = token
        response = app.get("/v1/intelligence/feed", params=params)
        assert response.status_code == 200, response.text
        assert UNSAFE_URL not in response.text
        pages.append(response.json())
        token = response.json()["meta"]["next_page_token"]
        if token is None:
            return pages


def test_skipped_rows_never_duplicate_or_drop_rows_across_pages() -> None:
    # Raw store order, page_size 4: page 1 reads rows 0-3 (+ lookahead row 4).
    # Row 1 is bad mid-page; row 4 is bad on the page-1/page-2 boundary.
    bad = (1, 4)
    with skip_log() as log:
        pages = _pages(PagedRepository(bad), 4)
    served = [[entry["item_id"] for entry in page["data"]] for page in pages]
    expected = [_identity(index + 1) for index in range(10) if index not in bad]
    assert len(pages) == 3
    # Short pages still carry a continuation while more raw rows exist.
    assert [len(page) for page in served] == [3, 3, 2]
    assert [page["meta"]["next_page_token"] is not None for page in pages] == [True, True, False]
    flattened = [item_id for page in served for item_id in page]
    assert flattened == expected
    assert len(set(flattened)) == len(flattened)
    skipped = [r.context["item_id"] for r in log.records if r.getMessage() == ROW_SKIPPED]
    assert skipped == [_identity(2), _identity(5)]
    assert UNSAFE_URL not in log.text

    # Tokens come from the rows read, so they match a clean read of the same store.
    clean_pages = _pages(PagedRepository(()), 4)
    assert [page["meta"]["next_page_token"] for page in pages] == [
        page["meta"]["next_page_token"] for page in clean_pages
    ]

    # Same newest-first order as the clean projection read in one page.
    clean = _pages(PagedRepository(()), 500)
    clean_order = [entry["item_id"] for entry in clean[0]["data"]]
    assert [item_id for item_id in clean_order if item_id in set(flattened)] == flattened


def test_skip_log_carries_identifiers_fields_and_count_only() -> None:
    repository = PagedRepository((0,))
    with skip_log() as log:
        response = client(repository).get("/v1/intelligence/feed")
    assert response.status_code == 200
    assert len(response.json()["data"]) == 9
    assert_row_skipped(
        log,
        route="feed",
        item_id=_identity(1),
        source_id="cdc-eid-expedited",
        field="canonical_url",
        forbidden=(UNSAFE_URL, "private-link", "javascript"),
    )
    (summary,) = [r for r in log.records if r.getMessage() == ROWS_SKIPPED]
    assert summary.levelname == "WARNING"
    assert summary.context == {"route": "feed", "skipped_count": 1, "served_count": 9}
    (row,) = [r for r in log.records if r.getMessage() == ROW_SKIPPED]
    assert row.context["fields"] == ["canonical_url"]
    assert row.context["error_class"] == "ValidationError"


def test_clean_page_logs_no_skip_signal() -> None:
    with skip_log() as log:
        response = client(PagedRepository(())).get("/v1/intelligence/feed")
    assert response.status_code == 200 and len(response.json()["data"]) == 10
    assert not [r for r in log.records if r.getMessage() in {ROW_SKIPPED, ROWS_SKIPPED}]


def test_every_row_failing_is_unavailable_not_empty() -> None:
    repository = PagedRepository(tuple(range(10)))
    with skip_log() as log:
        response = client(repository).get("/v1/intelligence/feed")
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert UNSAFE_URL not in response.text
    assert [r.context["skipped_count"] for r in log.records if r.getMessage() == ROWS_SKIPPED] == [
        10
    ]
    assert UNSAFE_URL not in log.text


@pytest.mark.parametrize("bad", [True, False])
def test_by_id_stays_fail_closed_for_an_unsafe_item(bad: bool) -> None:
    repository = PagedRepository((0,) if bad else ())
    response = client(repository).get(f"/v1/intelligence/feed/{_identity(1)}")
    assert response.status_code == (503 if bad else 200)
    assert UNSAFE_URL not in response.text


def test_items_skip_unsafe_row_and_fail_closed_when_all_rows_fail() -> None:
    repository = Repository()
    repository.document["canonical_url"] = UNSAFE_URL
    with skip_log() as log:
        response = items_client(repository).get("/v1/intelligence/items")
    assert response.status_code == 503 and UNSAFE_URL not in response.text
    assert [r.context["route"] for r in log.records if r.getMessage() == ROW_SKIPPED] == [
        "items",
        "items",
    ]
    assert UNSAFE_URL not in log.text


def test_failing_fields_report_schema_paths_without_row_values() -> None:
    from pydantic import BaseModel, ConfigDict

    class Inner(BaseModel):
        model_config = ConfigDict(extra="forbid")
        url: str

    class Outer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        links: list[Inner]
        named: dict[str, int]

    with pytest.raises(ValidationError) as caught:
        Outer.model_validate(
            {
                "links": [{"url": 1, "Private-Key name": "secret-value"}],
                "named": {"Not A Field": "secret-value"},
            }
        )
    fields = _failing_fields(caught.value)
    assert fields == ["links[]", "links[].url", "named.*"]
    assert "secret-value" not in str(fields) and "Private-Key" not in str(fields)

    assert _failing_fields(ValueError("INTELLIGENCE_SOURCE_FAMILY_INVALID")) == [
        "publisher_feed.source_family"
    ]
    assert _failing_fields(ValueError("secret-value")) == ["row"]
    assert _failing_fields(json.JSONDecodeError("secret-value", "{x", 1)) == ["variant_json"]
    assert _failing_fields(KeyError("secret-value")) == ["row"]


def test_skip_identity_only_echoes_identifier_shaped_values() -> None:
    assert _feed_row_identity(("a" * 64, "x", "cdc-eid-expedited", 1, "CDC", "c")) == (
        "a" * 64,
        "cdc-eid-expedited",
    )
    assert _feed_row_identity(()) == ("unavailable", "unavailable")
    assert _safe_identifier("https://example.org/secret") == "unavailable"
    assert _safe_identifier(None) == "unavailable"
    assert _item_row_identity(("<script>",)) == ("unavailable", "unavailable")


def test_items_accept_variant_columns_delivered_as_json_text() -> None:
    repository = Repository()
    original = repository.read

    def read(*args: Any) -> tuple[str, list[tuple[Any, ...]]]:
        marker, rows = original(*args)
        names = list(ITEM_COLUMNS)
        return marker, [
            tuple(
                json.dumps(value) if index < len(names) and names[index] in VARIANTS else value
                for index, value in enumerate(row)
            )
            for row in rows
        ]

    repository.read = read  # type: ignore[method-assign]
    response = items_client(repository).get("/v1/intelligence/items")
    assert response.status_code == 200 and response.json()["data"]
