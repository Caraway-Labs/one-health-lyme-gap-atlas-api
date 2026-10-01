"""Offline message rendering; no provider, subscriber or network composition."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from test_briefings import document, generate

from lyme_gap_atlas_api.briefings import DIGEST_NOTICE, BriefingArtifact
from lyme_gap_atlas_api.digest_rendering import (
    DigestRenderer,
    DigestRenderError,
    DigestRenderPrerequisites,
)


def prerequisites(artifact: BriefingArtifact, **changes: Any) -> DigestRenderPrerequisites:
    values = {
        name: getattr(artifact, name)
        for name in ("artifact_id", "input_sha256", "preference_revision", "digest_version")
    }
    values.update(changes)
    return DigestRenderPrerequisites.model_validate(values)


def render(artifact: BriefingArtifact) -> Any:
    return DigestRenderer(resolve_artifact=lambda required: artifact).render(
        prerequisites(artifact)
    )


@pytest.mark.parametrize("days", [1, 7])
def test_daily_weekly_frozen_artifacts_render_deterministically_without_calls(days: int) -> None:
    artifact = generate(
        [document()],
        window={
            "start": f"2026-09-{30 if days == 1 else 24}T00:00:00Z",
            "end": "2026-10-01T00:00:00Z",
        },
    )
    message = render(artifact)
    assert message == render(artifact)
    assert message.subject == "Atlas intelligence digest"
    assert message.artifact_id == artifact.artifact_id
    assert message.input_sha256 == artifact.input_sha256
    assert DIGEST_NOTICE in message.text and DIGEST_NOTICE in message.html
    with pytest.raises(FrozenInstanceError):
        message.subject = "changed"


def test_preserves_all_source_captures_matches_missingness_and_provenance() -> None:
    artifact = generate([document("rss"), document("atom")])
    message = render(artifact)
    assert "Scope:" in message.text and "Why matched:" in message.text
    assert "Window basis: fetched_at (not publication time)" in message.text
    for row in artifact.ranked_items:
        for evidence in row.evidence:
            item = evidence.item
            for value in (
                item.item_id,
                item.revision_id,
                item.fetched_at,
                item.source_id,
                item.provenance.artifact_id,
                item.provenance.artifact_sha256,
                item.provenance.parser_version,
                item.provenance.normalization_version,
                evidence.source.organization,
            ):
                assert value in message.text
            assert all(limitation in message.text for limitation in item.limitations)
    assert all(limitation in message.text for limitation in artifact.limitations)


def test_html_escapes_quotes_ampersands_and_does_not_execute_publisher_content() -> None:
    raw = document(
        title='Publisher "quoted" & text',
        excerpt="Use &lt;script&gt; as text",
        canonical_url='https://example.org/article?q="quoted"&next=1',
    )
    raw["field_states"]["excerpt"] = "present"
    message = render(generate([raw]))
    assert "&quot;quoted&quot; &amp; text" in message.html
    assert "&amp;lt;script&amp;gt;" in message.html
    assert 'href="https://example.org/article?q=&quot;quoted&quot;&amp;next=1"' in message.html
    assert "<script" not in message.html
    assert raw["title"] in message.text


def test_empty_and_truncated_digests_keep_truthful_window_and_limits() -> None:
    empty = render(generate([]))
    assert "No matching items in this frozen capture window." in empty.text
    assert "Matching publications: 0; omitted: 0" in empty.text
    other = document("atom", item_id="c" * 64, deduplication_key="c" * 64)
    truncated = render(generate([document("rss"), other], max_items=1))
    assert "omitted: 1" in truncated.text
    assert "Digest limitations:" in truncated.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("artifact_id", "c" * 64),
        ("input_sha256", "c" * 64),
        ("preference_revision", "different"),
        ("digest_version", "different"),
    ],
)
def test_exact_injected_artifact_binding_required(field: str, value: str) -> None:
    artifact = generate([document()])
    renderer = DigestRenderer(resolve_artifact=lambda required: artifact)
    with pytest.raises(DigestRenderError, match="BINDING_MISMATCH"):
        renderer.render(prerequisites(artifact, **{field: value}))


def test_missing_and_failed_resolvers_fail_closed_with_finite_diagnostics() -> None:
    artifact = generate([])
    required = prerequisites(artifact)
    with pytest.raises(DigestRenderError, match="ARTIFACT_UNAVAILABLE"):
        DigestRenderer(resolve_artifact=lambda required: None).render(required)

    def failure(required: DigestRenderPrerequisites) -> BriefingArtifact:
        raise RuntimeError("private-recipient-and-provider-secret")

    with pytest.raises(DigestRenderError) as caught:
        DigestRenderer(resolve_artifact=failure).render(required)
    assert str(caught.value) == "DIGEST_RENDER_RESOLVER_FAILED"
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_bypassed_validation_and_inconsistent_rank_or_count_are_rejected() -> None:
    artifact = generate([document()])
    invalid = artifact.model_copy(update={"notice": "unapproved"})
    with pytest.raises(DigestRenderError, match="ARTIFACT_INVALID"):
        render(invalid)
    for invalid in (
        artifact.model_copy(update={"matching_publications": 2}),
        artifact.model_copy(
            update={"ranked_items": (artifact.ranked_items[0].model_copy(update={"rank": 2}),)}
        ),
    ):
        with pytest.raises(DigestRenderError, match="ARTIFACT_INCONSISTENT"):
            render(invalid)


def test_missing_citation_is_explicit_and_no_atlas_link_is_invented() -> None:
    raw = document()
    raw["canonical_url"] = None
    raw["field_states"]["canonical_url"] = "not_provided"
    message = render(generate([raw]))
    assert "Canonical citation: unknown" in message.text
    assert "href=" not in message.html


def test_body_limit_fails_closed_without_trimming_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lyme_gap_atlas_api.digest_rendering.MAX_MESSAGE_BYTES", 10)
    with pytest.raises(DigestRenderError, match="BOUND_EXCEEDED"):
        render(generate([document()]))


def test_item_count_limit_precedes_rendering() -> None:
    artifact = generate([document()])
    oversized = artifact.model_copy(update={"ranked_items": artifact.ranked_items * 101})
    with pytest.raises(DigestRenderError, match="BOUND_EXCEEDED"):
        render(oversized)
