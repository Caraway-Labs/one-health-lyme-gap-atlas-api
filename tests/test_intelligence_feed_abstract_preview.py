"""List abstract previews and the by-id full abstract, using fake feed rows."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError
from test_intelligence_metadata_feed import _feed, _listed_and_detail

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.intelligence_feed import (
    ABSTRACT_PREVIEW_MAX_LENGTH,
    ABSTRACT_TEXT_MAX_LENGTH,
    FeedAbstract,
    FeedEntry,
    FeedListEntry,
    _project_abstract,
    cut_abstract_preview,
    entry_feed_description,
    list_feed_description,
    router,
)

_COPYRIGHT = "© 2026 The Publisher. All rights reserved."
_SOURCE = Path(__file__).resolve().parents[1] / "src/lyme_gap_atlas_api/intelligence_feed.py"


def _pubmed(text: str, **abstract: Any) -> dict[str, Any]:
    body = {"text": text, "state": "present", "copyright_line": _COPYRIGHT}
    body.update(abstract)
    return _feed(
        source_family="pubmed",
        abstract=body,
        source_item_id="41234567",
        description={"text": "not the abstract", "state": "present"},
    )


def _pair(text: str, **abstract: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    return _listed_and_detail(_pubmed(text, **abstract), publisher="Journal")


def test_long_abstract_is_previewed_on_the_list_and_complete_by_id() -> None:
    text = " ".join(f"word{index:04d}" for index in range(400))
    assert len(text) > ABSTRACT_PREVIEW_MAX_LENGTH
    assert len(text) < ABSTRACT_TEXT_MAX_LENGTH
    listed, detail = _pair(text)
    preview = listed["abstract_preview"]
    assert isinstance(preview, str)
    assert len(preview) <= ABSTRACT_PREVIEW_MAX_LENGTH
    assert preview.endswith("word") or preview[-1].isalnum()
    assert not preview.endswith(" ")
    assert listed["abstract_has_more"] is True
    assert listed["abstract_state"] == "present"
    assert listed["copyright_line"] == _COPYRIGHT
    assert "abstract" not in listed
    assert text not in json.dumps(listed)
    assert detail["abstract"]["text"] == text
    assert detail["abstract"]["state"] == "present"
    assert detail["abstract"]["copyright_line"] == _COPYRIGHT
    assert "abstract_preview" not in detail
    for entry in (listed, detail):
        assert entry["pubmed_url"] == "https://pubmed.ncbi.nlm.nih.gov/41234567/"
        assert entry["attribution"] == "Courtesy of the U.S. National Library of Medicine"


def test_short_abstract_is_returned_whole_on_the_list() -> None:
    text = "S" * 400
    listed, detail = _pair(text)
    assert listed["abstract_preview"] == text
    assert listed["abstract_has_more"] is False
    assert detail["abstract"]["text"] == text


def test_preview_word_boundary_cuts() -> None:
    limit = ABSTRACT_PREVIEW_MAX_LENGTH
    prefix = "a" * (limit - 5)
    straddling = prefix + " beta" + "c" * 40
    listed, _detail = _pair(straddling)
    assert listed["abstract_preview"] == prefix
    assert listed["abstract_has_more"] is True
    assert "beta" not in listed["abstract_preview"]

    punctuated = ("n" * (limit - 6)) + " end, " + ("b" * 40)
    listed, _detail = _pair(punctuated)
    assert listed["abstract_preview"] == ("n" * (limit - 6)) + " end"
    assert listed["abstract_preview"].endswith("end")
    assert listed["abstract_has_more"] is True

    landed_on_space = ("a" * limit) + " rest of the abstract"
    listed, _detail = _pair(landed_on_space)
    assert listed["abstract_preview"] == "a" * limit
    assert listed["abstract_has_more"] is True

    trailing_mark = ("a" * (limit - 1)) + ", more words here"
    listed, _detail = _pair(trailing_mark)
    assert listed["abstract_preview"] == "a" * (limit - 1)
    assert not listed["abstract_preview"].endswith(",")
    assert listed["abstract_has_more"] is True

    token = "a" * (limit + 80)
    listed, detail = _pair(token)
    assert listed["abstract_preview"] == ""
    assert listed["abstract_has_more"] is True
    assert token not in json.dumps(listed)
    assert detail["abstract"]["text"] == token


def test_preview_exactly_at_the_limit_is_complete() -> None:
    text = "b" * ABSTRACT_PREVIEW_MAX_LENGTH
    listed, _detail = _pair(text)
    assert listed["abstract_preview"] == text
    assert listed["abstract_has_more"] is False
    sentence = (("word " * 300)[: ABSTRACT_PREVIEW_MAX_LENGTH - 1]) + "."
    assert len(sentence) == ABSTRACT_PREVIEW_MAX_LENGTH
    listed, _detail = _pair(sentence)
    assert listed["abstract_preview"] == sentence
    assert listed["abstract_has_more"] is False


def test_preview_counts_characters_not_bytes() -> None:
    limit = ABSTRACT_PREVIEW_MAX_LENGTH
    exact = "é" * limit
    assert len(exact.encode("utf-8")) == limit * 2
    listed, _detail = _pair(exact)
    assert listed["abstract_preview"] == exact
    assert listed["abstract_has_more"] is False

    text = ("字" * (limit - 1)) + " " + ("字" * 30)
    listed, _detail = _pair(text)
    assert listed["abstract_preview"] == "字" * (limit - 1)
    assert listed["abstract_has_more"] is True
    assert listed["abstract_preview"].encode("utf-8").decode("utf-8") == listed["abstract_preview"]


def test_empty_not_provided_and_over_cap_on_both_routes() -> None:
    listed, detail = _listed_and_detail(
        _feed(
            source_family="pubmed",
            abstract={"text": "", "state": "empty"},
            source_item_id="41234567",
        ),
        publisher="Journal",
    )
    assert listed["abstract_preview"] is None
    assert listed["abstract_has_more"] is False
    assert listed["abstract_state"] == "empty"
    assert detail["abstract"]["state"] == "empty"
    assert detail["abstract"]["text"] == ""

    listed, detail = _listed_and_detail(
        _feed(
            source_family="pubmed",
            abstract={"text": None, "state": "not_provided"},
            source_item_id="41234567",
        ),
        publisher="Journal",
    )
    assert listed["abstract_state"] == "not_provided"
    assert listed["abstract_preview"] is None
    assert listed["abstract_has_more"] is False
    assert listed["copyright_line"] is None
    assert detail["abstract"]["state"] == "not_provided"
    assert detail["abstract"]["text"] is None

    over = "c" * (ABSTRACT_TEXT_MAX_LENGTH + 1)
    listed, detail = _pair(over)
    assert listed["abstract_state"] == "withheld"
    assert listed["abstract_preview"] is None
    assert listed["abstract_has_more"] is False
    assert listed["copyright_line"] is None
    assert detail["abstract"] == {"text": None, "state": "withheld", "copyright_line": None}
    assert over not in json.dumps(listed)
    assert over not in json.dumps(detail)


def test_suppressed_stays_suppressed_on_both_routes() -> None:
    listed, detail = _listed_and_detail(
        _feed(
            source_family="pubmed",
            abstract_suppressed=True,
            abstract={"text": "Hidden abstract", "state": "present", "copyright_line": _COPYRIGHT},
            source_item_id="41234567",
        ),
        publisher="Journal",
    )
    assert listed["abstract_state"] == "suppressed"
    assert listed["abstract_preview"] is None
    assert listed["abstract_has_more"] is False
    assert listed["copyright_line"] is None
    assert detail["abstract"]["state"] == "suppressed"
    assert detail["abstract"]["text"] is None
    assert "Hidden abstract" not in json.dumps(listed)
    assert "Hidden abstract" not in json.dumps(detail)


def test_pubmed_without_pmid_has_a_null_link_on_both_routes() -> None:
    listed, detail = _listed_and_detail(
        _feed(
            source_family="pubmed",
            abstract={"text": "No identifier", "state": "present", "copyright_line": None},
            source_item_id="not-a-pmid",
        ),
        publisher="Journal",
    )
    assert listed["pmid"] is None
    assert detail["pmid"] is None
    assert listed["pubmed_url"] is None
    assert detail["pubmed_url"] is None
    assert listed["copyright_line"] is None
    assert listed["attribution"] == detail["attribution"]


def test_unsafe_abstract_is_withheld_for_that_entry_only() -> None:
    listed, detail = _pair("alpha < beta")
    assert listed["abstract_state"] == "withheld"
    assert listed["abstract_preview"] is None
    assert detail["abstract"]["state"] == "withheld"
    assert "<" not in json.dumps(listed["abstract_preview"])


def _text_max_length(schema: dict[str, Any]) -> int:
    if "maxLength" in schema:
        return int(schema["maxLength"])
    for variant in schema.get("anyOf", []):
        if isinstance(variant, dict) and "maxLength" in variant:
            return int(variant["maxLength"])
    raise AssertionError(schema)


def test_changing_the_abstract_cap_changes_projection_model_and_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _SOURCE.read_text(encoding="utf-8")
    assert len(re.findall(r"6000", source)) == 1
    assert "ABSTRACT_TEXT_MAX_LENGTH: Final[int] = 6000" in source

    cap = 24
    monkeypatch.setattr(
        "lyme_gap_atlas_api.intelligence_feed.ABSTRACT_TEXT_MAX_LENGTH",
        cap,
    )
    accepted, accepted_failures = _project_abstract(
        {"text": "a" * cap, "state": "present", "copyright_line": _COPYRIGHT},
        pubmed=True,
        suppressed=False,
    )
    assert accepted_failures == 0
    assert accepted is not None
    assert accepted["state"] == "present"
    assert accepted["text"] == "a" * cap
    withheld, withheld_failures = _project_abstract(
        {"text": "a" * (cap + 1), "state": "present"},
        pubmed=True,
        suppressed=False,
    )
    assert withheld == {"text": None, "state": "withheld", "copyright_line": None}
    assert withheld_failures == 1

    FeedAbstract.model_validate({"text": "a" * cap, "state": "present", "copyright_line": None})
    with pytest.raises(ValidationError):
        FeedAbstract.model_validate(
            {"text": "a" * (cap + 1), "state": "present", "copyright_line": None}
        )
    schema = FeedAbstract.model_json_schema()
    text_schema = schema["properties"]["text"]
    assert _text_max_length(text_schema) == cap
    assert str(cap) in text_schema["description"]

    list_description = list_feed_description()
    entry_description = entry_feed_description()
    assert f"longer than {cap} characters" in list_description
    assert f"longer than {cap} characters" in entry_description
    routes = {
        route.operation_id: route
        for route in router.routes
        if isinstance(route, APIRoute) and route.operation_id
    }
    monkeypatch.setattr(routes["listIntelligenceFeed"], "description", list_description)
    monkeypatch.setattr(routes["getIntelligenceFeedEntry"], "description", entry_description)
    contract = create_app(settings=ApiSettings()).first_party_openapi()
    assert (
        f"longer than {cap} characters"
        in (contract["paths"]["/v1/intelligence/feed"]["get"]["description"])
    )
    assert (
        f"longer than {cap} characters"
        in (contract["paths"]["/v1/intelligence/feed/{item_id}"]["get"]["description"])
    )
    exported = contract["components"]["schemas"]["FeedAbstract"]["properties"]["text"]
    assert _text_max_length(exported) == cap
    assert str(cap) in exported["description"]


def _reject(model: Any, document: dict[str, Any], **changes: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**document, **changes})


def test_validators_reject_inconsistent_abstract_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listed, detail = _pair("Short abstract.")
    plain_listed, plain_detail = _listed_and_detail(_feed())

    _reject(FeedListEntry, listed, publication_date_state="not_provided")
    _reject(FeedListEntry, listed, attribution="Source: nope")
    _reject(FeedListEntry, listed, pubmed_url=None)
    _reject(
        FeedListEntry,
        plain_listed,
        pmid="123",
        pubmed_url="https://pubmed.ncbi.nlm.nih.gov/123/",
    )
    _reject(FeedEntry, detail, abstract=None)
    _reject(
        FeedEntry,
        plain_detail,
        abstract={"text": None, "state": "withheld", "copyright_line": None},
    )
    _reject(FeedListEntry, plain_listed, abstract_state="not_provided")
    _reject(
        FeedListEntry,
        listed,
        abstract_state=None,
        abstract_preview=None,
        abstract_has_more=False,
        copyright_line=None,
    )
    _reject(
        FeedListEntry,
        listed,
        abstract_state="withheld",
        abstract_preview="kept",
        abstract_has_more=False,
        copyright_line=None,
    )
    _reject(FeedListEntry, listed, abstract_preview=None)
    _reject(FeedListEntry, listed, abstract_preview="", abstract_has_more=False)
    monkeypatch.setattr(
        "lyme_gap_atlas_api.intelligence_feed.ABSTRACT_PREVIEW_MAX_LENGTH",
        4,
    )
    _reject(FeedListEntry, listed, abstract_preview="hello", abstract_has_more=True)

    with pytest.raises(ValidationError):
        FeedAbstract.model_validate({"text": "", "state": "present", "copyright_line": None})
    with pytest.raises(ValidationError):
        FeedAbstract.model_validate({"text": "", "state": "empty", "copyright_line": "© 2026"})
    with pytest.raises(ValidationError):
        FeedAbstract.model_validate({"text": "x", "state": "withheld", "copyright_line": None})


def test_preview_helper_matches_the_named_limit() -> None:
    preview, has_more = cut_abstract_preview("alpha beta", limit=6)
    assert preview == "alpha"
    assert has_more is True
    whole, has_more = cut_abstract_preview("alpha", limit=ABSTRACT_PREVIEW_MAX_LENGTH)
    assert whole == "alpha" and has_more is False
