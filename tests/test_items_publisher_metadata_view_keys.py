"""/items under the v2 projection: FEED_V2 view-only publisher_metadata keys.

Data V173 (data#845) adds description, abstract and abstract_suppressed. Data V175
(data#854) adds mesh, publication_types and matched_queries. /items checks them
strictly and does not return them. Any other unknown key is still rejected.
All rows are synthetic; no real feed data is used.
"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from _feed_skip import HEALTHY_ITEM_ID, assert_row_skipped, healthy_copy, skip_log
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import (
    ABSTRACT_TEXT_MAX_LENGTH,
    V2_COLUMNS,
    FeedAbstract,
    FeedDescription,
)
from lyme_gap_atlas_api.intelligence_projection import (
    FEED_V2_VIEW_ONLY_KEYS,
    PublisherMetadata,
)

FIXTURE = Path(__file__).parent / "fixtures/intelligence/rss-item.json"
NLM = "Courtesy of the U.S. National Library of Medicine"
POLICY = "https://www.ncbi.nlm.nih.gov/home/about/policies/"
PMID = "41000001"


def _v2_document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(FIXTURE.read_text())
    document.update(
        contract_version="2.0.0",
        publisher_metadata={
            "publisher": "CDC",
            "source_family": "cdc_eid",
            "source_item_id": None,
            "authors": ["Publisher Author"],
            "categories": ["Tick borne research"],
            "language": "en",
            "media": [],
            "date_states": {
                key: document["field_states"][key] for key in ("published_at", "updated_at")
            },
        },
        derived_metadata={},
    )
    document["provenance"].update(
        parser_version="rss-atom-native-v2", normalization_version="intelligence-identity-v2"
    )
    return document


def rss_row() -> dict[str, Any]:
    """V173 RSS row: the three new keys are JSON null."""
    document = _v2_document()
    document["publisher_metadata"].update(description=None, abstract=None, abstract_suppressed=None)
    return document


def pubmed_abstract(**changes: Any) -> dict[str, Any]:
    abstract = {
        "text": "Synthetic abstract about tick-borne disease surveillance.",
        "state": "present",
        "copyright_line": "Copyright 2026 Synthetic Authors.",
        "attribution": NLM,
        "policy_url": POLICY,
    }
    abstract.update(changes)
    return abstract


def pubmed_row(**metadata: Any) -> dict[str, Any]:
    """V175 PubMed row with the stored abstract object and the PubMed arrays."""
    document = _v2_document()
    document["provenance"]["parser_version"] = "entrez-efetch-v1"
    document["publisher_metadata"].update(
        publisher="Synthetic Journal",
        source_family="pubmed",
        source_item_id=PMID,
        description=None,
        abstract=pubmed_abstract(),
        abstract_suppressed=False,
        mesh=[{"name": "Lyme Disease", "ui": "D008193"}, {"name": "Ticks", "ui": "D013987"}],
        publication_types=["Journal Article"],
        matched_queries=["pubmed-tick-borne", "pubmed-lyme-borrelia"],
    )
    document["publisher_metadata"].update(metadata)
    return document


class V2Repository:
    def __init__(self, *documents: dict[str, Any]) -> None:
        self.documents = list(documents)

    def read(
        self, kind: str, source_id: Any, start: Any, end: Any, offset: int, size: int
    ) -> tuple[str, list[tuple[Any, ...]]]:
        assert kind == "items"
        rows = [
            tuple(document[name] for name in V2_COLUMNS) + ("CDC", "official_public_health")
            for document in self.documents
        ]
        return "v2", rows[offset : offset + size + 1]


def client(repository: V2Repository) -> TestClient:
    return TestClient(
        create_app(
            settings=ApiSettings(
                intelligence_feed_read_enabled=True, intelligence_feed_projection_version="v2"
            ),
            intelligence_repository=repository,
        )
    )


PUBLIC_KEYS = {
    "publisher",
    "source_family",
    "source_item_id",
    "authors",
    "categories",
    "language",
    "media",
    "date_states",
}


def test_view_only_keys_are_exactly_the_v173_and_v175_keys() -> None:
    view_only = set(FEED_V2_VIEW_ONLY_KEYS)
    assert view_only == {
        "description",
        "abstract",
        "abstract_suppressed",
        "mesh",
        "publication_types",
        "matched_queries",
    }
    assert set(PublisherMetadata.model_fields) == PUBLIC_KEYS


def test_rss_pubmed_suppressed_and_eight_key_rows_are_served_without_the_keys() -> None:
    eight_key = _v2_document()
    eight_key["item_id"] = "e" * 64
    eight_key["deduplication_key"] = "e" * 64
    rss = rss_row()
    pubmed = pubmed_row()
    pubmed["item_id"] = "d" * 64
    pubmed["deduplication_key"] = "d" * 64
    suppressed = pubmed_row(
        abstract={"text": None, "state": "suppressed", "copyright_line": None},
        abstract_suppressed=True,
    )
    suppressed["item_id"] = "f" * 64
    suppressed["deduplication_key"] = "f" * 64
    described = rss_row()
    described["item_id"] = "b" * 64
    described["deduplication_key"] = "b" * 64
    described["publisher_metadata"]["description"] = {
        "text": "Synthetic publisher summary.",
        "state": "present",
        "format": "plain_text",
        "origin": "publisher",
    }

    with skip_log() as log:
        response = client(V2Repository(rss, pubmed, suppressed, eight_key, described)).get(
            "/v1/intelligence/items"
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data) == 5
    for entry in data:
        assert set(entry["item"]["publisher_metadata"]) == PUBLIC_KEYS
    assert "Synthetic abstract" not in response.text
    assert "Synthetic publisher summary" not in response.text
    assert "D008193" not in response.text
    assert "intelligence_feed_row_skipped" not in log.text


@pytest.mark.parametrize(
    ("state", "text", "copyright_line"),
    [
        ("not_provided", None, None),
        ("withheld", None, None),
        ("present", "x" * ABSTRACT_TEXT_MAX_LENGTH, None),
    ],
)
def test_other_valid_abstract_states(state: str, text: Any, copyright_line: Any) -> None:
    document = pubmed_row(abstract=pubmed_abstract(state=state, text=text, copyright_line=None))
    response = client(V2Repository(document)).get("/v1/intelligence/items")
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


@pytest.mark.parametrize(
    "description",
    [
        {"text": "", "state": "empty", "format": "plain_text", "origin": "publisher"},
        {"text": None, "state": "not_provided", "format": "plain_text", "origin": "publisher"},
        {"text": "y" * 4096, "state": "present", "format": "plain_text", "origin": "publisher"},
    ],
)
def test_other_valid_descriptions(description: dict[str, Any]) -> None:
    document = rss_row()
    document["publisher_metadata"]["description"] = description
    response = client(V2Repository(document)).get("/v1/intelligence/items")
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


PRIVATE = "private-row-value"
DESCRIPTION = {
    "text": "Summary.",
    "state": "present",
    "format": "plain_text",
    "origin": "publisher",
}

INVALID: list[tuple[str, Any]] = [
    # Fail-closed: unknown keys, including capture-only keys V173/V175 do not project.
    ("journal", PRIVATE),
    ("doi", PRIVATE),
    ("entry_date", PRIVATE),
    ("native_metadata", {"raw": PRIVATE}),
    # abstract_suppressed is a strict boolean.
    ("abstract_suppressed", "true"),
    ("abstract_suppressed", 1),
    ("abstract_suppressed", PRIVATE),
    # abstract shape, bounds and unsafe text.
    ("abstract", PRIVATE),
    ("abstract", pubmed_abstract(text="x" * (ABSTRACT_TEXT_MAX_LENGTH + 1))),
    ("abstract", pubmed_abstract(text=f"<script>{PRIVATE}</script>")),
    ("abstract", pubmed_abstract(text=f"{PRIVATE}\x00")),
    ("abstract", pubmed_abstract(text=None)),
    ("abstract", pubmed_abstract(text="")),
    ("abstract", pubmed_abstract(state="empty", text="")),
    ("abstract", pubmed_abstract(state="suppressed", text=PRIVATE, copyright_line=None)),
    ("abstract", pubmed_abstract(state="withheld", text=None, copyright_line=PRIVATE)),
    ("abstract", pubmed_abstract(state="unknown")),
    ("abstract", pubmed_abstract(copyright_line="c" * 1001)),
    ("abstract", pubmed_abstract(copyright_line=f"<b>{PRIVATE}</b>")),
    ("abstract", pubmed_abstract(attribution=PRIVATE)),
    ("abstract", pubmed_abstract(attribution=None)),
    ("abstract", pubmed_abstract(policy_url="https://example.org/policy")),
    ("abstract", pubmed_abstract(email=PRIVATE)),
    ("abstract", pubmed_abstract(text=123)),
    # description shape, bounds and unsafe text.
    ("description", PRIVATE),
    ("description", {**DESCRIPTION, "text": "y" * 4097}),
    ("description", {**DESCRIPTION, "text": f"{PRIVATE}\x07"}),
    ("description", {**DESCRIPTION, "text": f"<img src={PRIVATE}>"}),
    ("description", {**DESCRIPTION, "format": "html"}),
    ("description", {**DESCRIPTION, "origin": "derived"}),
    ("description", {**DESCRIPTION, "state": "withheld", "text": None}),
    ("description", {**DESCRIPTION, "state": "present", "text": ""}),
    ("description", {**DESCRIPTION, "state": "empty", "text": PRIVATE}),
    ("description", {**DESCRIPTION, "state": "not_provided", "text": PRIVATE}),
    ("description", {key: value for key, value in DESCRIPTION.items() if key != "format"}),
    ("description", {**DESCRIPTION, "url": f"https://example.org/?token={PRIVATE}"}),
    # mesh.
    ("mesh", [{"name": "Ticks", "ui": "D013987"}] * 51),
    ("mesh", [{"name": "Ticks", "ui": "d013987"}]),
    ("mesh", [{"name": "Ticks", "ui": "D0139870"}]),
    ("mesh", [{"name": "", "ui": "D013987"}]),
    ("mesh", [{"name": "n" * 1001, "ui": "D013987"}]),
    ("mesh", [{"name": f"<{PRIVATE}>", "ui": "D013987"}]),
    ("mesh", [{"name": "Ticks", "ui": "D013987", "note": PRIVATE}]),
    ("mesh", PRIVATE),
    # publication_types.
    ("publication_types", ["Journal Article"] * 51),
    ("publication_types", [""]),
    ("publication_types", ["t" * 257]),
    ("publication_types", [f"{PRIVATE}\n"]),
    ("publication_types", [7]),
    # matched_queries.
    ("matched_queries", [PRIVATE]),
    ("matched_queries", ["pubmed-tick-borne", "pubmed-tick-borne"]),
    ("matched_queries", ["pubmed-tick-borne", "pubmed-lyme-borrelia", "pubmed-tick-borne"]),
    ("matched_queries", "pubmed-tick-borne"),
]


@pytest.mark.parametrize(("key", "value"), INVALID)
def test_invalid_view_keys_skip_the_row_and_log_no_value(key: str, value: Any) -> None:
    document = pubmed_row()
    healthy = healthy_copy(document)
    document["publisher_metadata"][key] = deepcopy(value)
    with skip_log() as log:
        response = client(V2Repository(document, healthy)).get("/v1/intelligence/items")
    assert response.status_code == 200
    assert [entry["item"]["item_id"] for entry in response.json()["data"]] == [HEALTHY_ITEM_ID]
    assert PRIVATE not in response.text
    assert_row_skipped(
        log,
        route="items",
        item_id=document["item_id"],
        source_id=document["source_id"],
        field="publisher_metadata",
        forbidden=(PRIVATE, "<script>", "D0139870"),
    )


def test_every_row_failing_stays_503() -> None:
    document = rss_row()
    document["publisher_metadata"]["abstract_suppressed"] = "true"
    response = client(V2Repository(document)).get("/v1/intelligence/items")
    assert response.status_code == 503


def test_reuses_feed_text_models() -> None:
    # The stored objects are held to the /feed response models' rules.
    long_text = "x" * (ABSTRACT_TEXT_MAX_LENGTH + 1)
    with pytest.raises(ValueError):
        FeedAbstract.model_validate({"text": long_text, "state": "present", "copyright_line": None})
    with pytest.raises(ValueError):
        FeedDescription.model_validate({"text": "y" * 4097, "state": "present"})
    base = _v2_document()["publisher_metadata"]
    for key, value in INVALID:
        if key in FEED_V2_VIEW_ONLY_KEYS:
            with pytest.raises(ValidationError) as caught:
                PublisherMetadata.model_validate({**base, key: value})
            errors = caught.value.errors(include_input=False)
            assert [error["msg"] for error in errors] == [
                "Value error, INTELLIGENCE_PUBLISHER_METADATA_INVALID"
            ]


def test_projection_setting_default_and_app_spec_unchanged() -> None:
    assert ApiSettings.model_fields["intelligence_feed_projection_version"].default == "v1"
    spec = Path(__file__).parents[1] / ".do/app.yaml"
    if spec.exists():
        assert "INTELLIGENCE_FEED_PROJECTION_VERSION" not in spec.read_text()


def test_non_mapping_publisher_metadata_still_fails_closed() -> None:
    for value in ("not-a-mapping", ["description"], None):
        with pytest.raises(ValidationError) as caught:
            PublisherMetadata.model_validate(value)
        assert all(error["type"] == "model_type" for error in caught.value.errors())
