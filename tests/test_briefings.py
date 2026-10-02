"""Independent structured-artifact contract and deterministic generation outcomes."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.briefings import (
    DIGEST_NOTICE,
    BriefingArtifact,
    BriefingError,
    BriefingGenerationRequest,
    BriefingService,
    BriefingWindow,
    InMemoryBriefingStore,
    IntelligenceItem,
    IntelligenceSnapshot,
    IntelligenceTag,
)
from lyme_gap_atlas_api.config import ApiSettings

FIXTURES = Path(__file__).parent / "fixtures/intelligence"


def document(transport: str = "rss", **changes: Any) -> dict[str, Any]:
    values = json.loads((FIXTURES / f"{transport}-item.json").read_text())
    values.update(changes)
    return values


def request(**changes: Any) -> BriefingGenerationRequest:
    values: dict[str, Any] = {
        "subscriber_key": "a" * 64,
        "preference_revision": "preferences-1",
        "digest_version": "digest-1",
        "window": {"start": "2026-09-30T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
        "scope": {},
    }
    values.update(changes)
    return BriefingGenerationRequest.model_validate(values)


def snapshot(items: list[dict[str, Any]], **changes: Any) -> IntelligenceSnapshot:
    sources = {
        (item["source_id"], item["registry_version"]): {
            "source_id": item["source_id"],
            "registry_version": item["registry_version"],
            "organization": "Synthetic publisher",
            "reviewed_trust_classification": "research_index",
        }
        for item in items
    }
    values: dict[str, Any] = {
        "snapshot_id": "b" * 64,
        "as_of": "2026-10-01T00:00:00Z",
        "items": items,
        "sources": list(sources.values()),
    }
    values.update(changes)
    return IntelligenceSnapshot.model_validate(values)


def generate(items: list[dict[str, Any]], **changes: Any) -> BriefingArtifact:
    return BriefingService(InMemoryBriefingStore()).generate(
        request(**changes),
        snapshot(items),
        generated_at="2026-10-01T00:01:00Z",
    )


@pytest.mark.parametrize("transport", ["rss", "atom", "email", "web"])
def test_accepted_data131_examples_preserve_every_public_field(transport: str) -> None:
    original = document(transport)
    assert IntelligenceItem.model_validate(original).model_dump(mode="json") == original


def test_empty_digest_retains_window_scope_versions_and_non_alert_limits() -> None:
    artifact = generate([], scope={"topics": ["tick-borne"], "geographies": ["county:08001"]})
    assert artifact.ranked_items == () and artifact.matching_publications == 0
    assert artifact.window == request().window
    assert artifact.scope.topics == ("tick-borne",)
    assert artifact.preference_revision == "preferences-1" and artifact.digest_version == "digest-1"
    assert artifact.contract_version == "1.0.0" and artifact.notice == DIGEST_NOTICE
    assert artifact.mode == "digest" and any(
        "absence of disease" in text for text in artifact.limitations
    )
    assert "subscriber_key" not in artifact.model_dump()


def test_matching_requires_each_selected_dimension_with_any_value_inside_dimension() -> None:
    original = document()
    assert generate([original], scope={"geographies": ["county:08001"]}).ranked_items == ()
    assert generate([original], scope={"topics": ["unknown"]}).ranked_items == ()
    assert generate([original], scope={"sources": ["other"]}).ranked_items == ()
    original["geographies"] = [
        {
            "value": "county:08001",
            "origin": "publisher",
            "method": None,
            "method_version": None,
            "confidence": None,
        },
    ]
    artifact = generate(
        [original],
        scope={
            "geographies": ["county:08001", "county:08003"],
            "topics": ["tick-borne"],
            "sources": ["synthetic-publication"],
        },
    )
    evidence = artifact.ranked_items[0].evidence[0]
    assert {(match.dimension, match.value, match.origin) for match in evidence.why_matched} == {
        ("geography", "county:08001", "publisher"),
        ("topic", "tick-borne", "publisher"),
        ("source", "synthetic-publication", "source_identity"),
    }
    assert evidence.item.provenance.model_dump() == original["provenance"]
    assert evidence.item.geographies[0].confidence is None


def test_duplicate_polls_share_one_ranked_article_with_all_revision_source_provenance() -> None:
    first = document()
    repoll = copy.deepcopy(first)
    repoll["fetched_at"] = "2026-09-30T12:00:00Z"
    repoll["provenance"].update(run_id="repoll", artifact_id="repoll-artifact")
    other = copy.deepcopy(first)
    other.update(source_id="another-source", transport="atom")
    other["provenance"].update(run_id="atom-run", artifact_id="atom-artifact")
    correction = copy.deepcopy(first)
    correction.update(revision_id="c" * 64, content_sha256="d" * 64, title="Publisher correction")
    artifact = generate([first, first, repoll, other, correction])
    assert len(artifact.ranked_items) == artifact.matching_publications == 1
    ranked = artifact.ranked_items[0]
    assert len(ranked.evidence) == 4 and ranked.rank == 1
    assert {evidence.item.revision_id for evidence in ranked.evidence} == {
        first["revision_id"],
        "c" * 64,
    }
    assert {evidence.item.source_id for evidence in ranked.evidence} == {
        "synthetic-publication",
        "another-source",
    }
    assert {evidence.item.title for evidence in ranked.evidence} == {
        first["title"],
        "Publisher correction",
    }
    assert all(evidence.item.content_is_untrusted for evidence in ranked.evidence)


def test_rank_match_count_then_actual_publisher_recency_then_stable_identity() -> None:
    first = document(item_id="1" * 64, deduplication_key="1" * 64)
    tied = document(item_id="2" * 64, deduplication_key="2" * 64)
    older_stronger = document(
        item_id="3" * 64, deduplication_key="3" * 64, published_at="2026-01-01T00:00:00Z"
    )
    older_stronger["topics"].append(
        {
            "value": "lyme",
            "origin": "publisher",
            "method": None,
            "method_version": None,
            "confidence": None,
        }
    )
    newest = document(
        item_id="4" * 64, deduplication_key="4" * 64, published_at="2026-09-30T00:00:00Z"
    )
    artifact = generate(
        [tied, newest, first, older_stronger], scope={"topics": ["lyme", "tick-borne"]}, max_items=3
    )
    assert [publication.item_id for publication in artifact.ranked_items] == [
        "3" * 64,
        "4" * 64,
        "1" * 64,
    ]
    assert [publication.rank for publication in artifact.ranked_items] == [1, 2, 3]
    assert artifact.matching_publications == 4 and artifact.omitted_publications == 1


def test_unknown_publisher_chronology_is_not_replaced_by_fetch_time() -> None:
    unknown = document(published_at=None)
    unknown["field_states"]["published_at"] = "not_provided"
    ranked = generate([unknown]).ranked_items[0]
    assert ranked.publisher_recency is None
    assert ranked.evidence[0].item.published_at is None
    assert ranked.evidence[0].item.fetched_at == unknown["fetched_at"]


@pytest.mark.parametrize("days", [1, 7])
def test_daily_and_weekly_share_half_open_cutoffs_and_keep_delayed_items(days: int) -> None:
    start = f"2026-09-{30 if days == 1 else 24}T00:00:00Z"
    at_start = document(item_id="1" * 64, deduplication_key="1" * 64, fetched_at=start)
    at_end = document(
        item_id="2" * 64, deduplication_key="2" * 64, fetched_at="2026-10-01T00:00:00Z"
    )
    old = document(item_id="3" * 64, deduplication_key="3" * 64, fetched_at="2026-09-23T23:59:59Z")
    artifact = generate(
        [at_start, at_end, old], window={"start": start, "end": "2026-10-01T00:00:00Z"}
    )
    assert [publication.item_id for publication in artifact.ranked_items] == ["1" * 64]
    assert artifact.ranked_items[0].evidence[0].item.published_at == at_start["published_at"]


def test_fractional_cutoff_keeps_full_precision_and_correct_zero_fraction_order() -> None:
    window = {"start": "2026-09-30T08:00:00Z", "end": "2026-09-30T08:00:00.0000000002Z"}
    included = document(fetched_at="2026-09-30T08:00:00.0000000001Z")
    excluded = document(item_id="2" * 64, deduplication_key="2" * 64, fetched_at=window["end"])
    artifact = generate([included, excluded], window=window)
    assert len(artifact.ranked_items) == 1
    assert artifact.ranked_items[0].evidence[0].item.fetched_at == included["fetched_at"]


def test_idempotent_generation_retains_first_timestamp_despite_reordered_inputs() -> None:
    service = BriefingService(InMemoryBriefingStore())
    original = document()
    first = service.generate(
        request(scope={"topics": ["lyme", "tick-borne"]}),
        snapshot([original]),
        generated_at="2026-10-01T00:01:00Z",
    )
    again = service.generate(
        request(scope={"topics": ["tick-borne", "lyme"]}),
        snapshot([original, original]),
        generated_at="2026-10-01T01:00:00Z",
    )
    assert first == again and again.generated_at == "2026-10-01T00:01:00Z"
    changed = document(title="Different publisher content")
    with pytest.raises(BriefingError, match="IDEMPOTENCY_CONFLICT") as caught:
        service.generate(
            request(scope={"topics": ["lyme", "tick-borne"]}),
            snapshot([changed]),
            generated_at="2026-10-01T00:02:00Z",
        )
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    bumped = service.generate(
        request(digest_version="digest-2"), snapshot([changed]), generated_at="2026-10-01T00:02:00Z"
    )
    assert bumped.artifact_id != first.artifact_id
    preference_bumped = service.generate(
        request(preference_revision="preferences-2"),
        snapshot([original]),
        generated_at="2026-10-01T00:02:00Z",
    )
    assert preference_bumped.artifact_id not in {first.artifact_id, bumped.artifact_id}


def test_concurrent_generation_returns_one_immutable_artifact_in_test_store() -> None:
    service = BriefingService(InMemoryBriefingStore())

    def produce(minute: int) -> BriefingArtifact:
        return service.generate(
            request(), snapshot([document()]), generated_at=f"2026-10-01T00:{minute:02}:00Z"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        artifacts = list(pool.map(produce, [1, 2]))
    assert artifacts[0] == artifacts[1]


@pytest.mark.parametrize(
    "case",
    [
        "missing_source",
        "duplicate_source",
        "before_cutoff",
        "future_capture",
        "conflicting_capture",
    ],
)
def test_inconsistent_snapshots_fail_closed(case: str) -> None:
    original = document()
    values = snapshot([original]).model_dump(mode="json")
    if case == "missing_source":
        values["sources"] = []
    elif case == "duplicate_source":
        values["sources"] *= 2
    elif case == "before_cutoff":
        values["as_of"] = "2026-09-30T23:00:00Z"
    elif case == "future_capture":
        values["items"][0]["fetched_at"] = "2026-10-01T00:01:00Z"
    else:
        changed = copy.deepcopy(original)
        changed["limitations"].append("Changed capture evidence")
        values["items"].append(changed)
    with pytest.raises(BriefingError):
        BriefingService(InMemoryBriefingStore()).generate(
            request(),
            IntelligenceSnapshot.model_validate(values),
            generated_at="2026-10-01T00:01:00Z",
        )


@pytest.mark.parametrize(
    "bad",
    [
        "2026-09-31T00:00:00Z",
        "2026-09-30T00:00:00+01:00",
        "2026-09-30T00:00:00.10Z",
        "2026-09-30T00:00:60Z",
    ],
)
def test_invalid_or_noncanonical_utc_is_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        BriefingWindow(start=bad, end="2026-10-01T00:00:00Z")


def test_window_limits_and_alert_mode_rejection() -> None:
    for start, end in [
        ("2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"),
        ("2026-09-01T00:00:00Z", "2026-10-02T00:00:00.0000000001Z"),
    ]:
        with pytest.raises(ValidationError):
            BriefingWindow(start=start, end=end)
    with pytest.raises(ValidationError):
        request(mode="alert")
    with pytest.raises(ValidationError):
        request(scope={"topics": ["tick-borne", "tick-borne"]})


@pytest.mark.parametrize(
    "change",
    [
        {"mailbox": "private@example.org"},
        {"title": "<script>unsafe</script>"},
        {"content_is_untrusted": False},
        {"registry_version": True},
        {"canonical_url": "https://user:secret@example.org/"},
    ],
)
def test_private_unknown_unsafe_or_misleading_item_fields_are_rejected(
    change: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        IntelligenceItem.model_validate(document(**change))


def test_field_states_and_tag_origin_require_evidence() -> None:
    with pytest.raises(ValidationError):
        IntelligenceItem.model_validate(document(published_at=None))
    tag = {
        "value": "county:08001",
        "origin": "inferred",
        "method": None,
        "method_version": None,
        "confidence": None,
    }
    with pytest.raises(ValidationError):
        IntelligenceTag.model_validate(tag)
    tag.update(method="reviewed-method", method_version="1")
    assert IntelligenceTag.model_validate(tag).confidence is None
    tag["origin"] = "publisher"
    with pytest.raises(ValidationError):
        IntelligenceTag.model_validate(tag)


def test_external_store_failure_is_redacted_without_raw_exception_chain() -> None:
    class UnavailableStore:
        def insert_if_absent(self, artifact: BriefingArtifact) -> BriefingArtifact:
            raise RuntimeError("private warehouse credential")

    with pytest.raises(BriefingError) as caught:
        BriefingService(UnavailableStore()).generate(
            request(), snapshot([]), generated_at="2026-10-01T00:01:00Z"
        )
    assert str(caught.value) == "BRIEFING_STORE_UNAVAILABLE"
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_store_cannot_return_changed_content_with_the_same_identity_and_checksum() -> None:
    class WrongReceiptStore:
        def insert_if_absent(self, artifact: BriefingArtifact) -> BriefingArtifact:
            return artifact.model_copy(update={"matching_publications": 99})

    with pytest.raises(BriefingError, match="STORE_RECEIPT_INVALID") as caught:
        BriefingService(WrongReceiptStore()).generate(
            request(),
            snapshot([]),
            generated_at="2026-10-01T00:01:00Z",
        )
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_generation_timestamp_cannot_precede_input_snapshot() -> None:
    with pytest.raises(BriefingError, match="GENERATION_BEFORE_SNAPSHOT"):
        BriefingService(InMemoryBriefingStore()).generate(
            request(),
            snapshot([]),
            generated_at="2026-09-30T23:59:59Z",
        )


def test_snapshot_item_count_bound_is_explicit_in_the_contract() -> None:
    with pytest.raises(ValidationError):
        snapshot([document()] * 1001)


def test_openapi_contains_versioned_contracts_without_subscriber_route_or_changed_paths() -> None:
    schema = create_app(settings=ApiSettings()).first_party_openapi()
    models = schema["components"]["schemas"]
    artifact = models["BriefingArtifact"]
    assert artifact["properties"]["contract_version"]["const"] == "1.0.0"
    assert artifact["properties"]["mode"]["const"] == "digest"
    assert artifact["additionalProperties"] is False
    assert models["BriefingGenerationRequest"]["properties"]["mode"]["const"] == "digest"
    assert {"window", "scope", "ranked_items", "limitations", "input_sha256", "snapshot_id"} <= set(
        artifact["required"]
    )
    assert {"item", "source", "why_matched"} == set(models["BriefingEvidence"]["required"])
    assert not any(
        "briefing" in path or "subscriber" in path or "digest" in path for path in schema["paths"]
    )
    assert create_app(settings=ApiSettings()).first_party_openapi() == schema


def test_limitation_reordering_and_duplicates_replay_the_first_complete_artifact() -> None:
    service = BriefingService(InMemoryBriefingStore())
    first = service.generate(
        request(),
        snapshot([document()], limitations=["z limitation", "a limitation"]),
        generated_at="2026-10-01T00:01:00Z",
    )
    replay = service.generate(
        request(),
        snapshot([document()], limitations=["a limitation", "z limitation", "a limitation"]),
        generated_at="2026-10-01T00:02:00Z",
    )
    assert replay == first
    assert first.limitations[-2:] == ("a limitation", "z limitation")


@pytest.mark.parametrize("timestamp", ["2000-01-01T00:00:00Z", "malformed"])
def test_store_receipt_generation_time_must_be_valid_and_reach_snapshot(timestamp: str) -> None:
    class EarlyReceiptStore:
        def insert_if_absent(self, artifact: BriefingArtifact) -> BriefingArtifact:
            return artifact.model_copy(update={"generated_at": timestamp})

    with pytest.raises(BriefingError):
        BriefingService(EarlyReceiptStore()).generate(
            request(),
            snapshot([]),
            generated_at="2026-10-01T00:01:00Z",
        )


@pytest.mark.parametrize("confidence", [True, False, "1", "0.5"])
def test_normalized_confidence_does_not_coerce_boolean_or_text(confidence: Any) -> None:
    with pytest.raises(ValidationError):
        IntelligenceTag(
            value="county:08001",
            origin="inferred",
            method="reviewed-method",
            method_version="1",
            confidence=confidence,
        )


@pytest.mark.parametrize("confidence", [0, 1, 0.5, None])
def test_normalized_confidence_accepts_json_numbers_and_unknown(confidence: Any) -> None:
    assert (
        IntelligenceTag(
            value="county:08001",
            origin="inferred",
            method="reviewed-method",
            method_version="1",
            confidence=confidence,
        ).confidence
        == confidence
    )
