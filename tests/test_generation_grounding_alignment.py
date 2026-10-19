"""The generation instructions target the existing deterministic claim contract."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lyme_gap_atlas_api.knowledge_chat import (
    Evidence,
    GenerationBudgetExceeded,
    GenerationReferenceError,
    KnowledgeChatService,
    OpenAIAnswerer,
    _generation_schema,
    _quote_segments,
    _resolve_generation,
    _shape_diagnostics,
    _validate_claim_text,
    _validate_evidence_state,
    _validate_grounding,
)
from lyme_gap_atlas_api.models import KnowledgeChatRequest

EXCERPT = "In Germany, Ixodes abundance was associated with Borrelia prevalence."
EVIDENCE = [
    Evidence(
        "passage-1",
        EXCERPT,
        "Governed full-text passage",
        "12345678",
        "Paper 12345678",
        "https://pubmed.ncbi.nlm.nih.gov/12345678/",
    )
]


def generated_claim() -> dict[str, Any]:
    return {
        "answer": "A broad synthesis that the service must not display.",
        "evidence_state": "single_study",
        "claims": [
            {
                "claim_id": "claim-1",
                "text": "Ixodes abundance was linked to Borrelia prevalence in Germany.",
                "passage_ids": ["passage-1"],
                "pmids": ["12345678"],
                "support_quotes": {"passage-1": EXCERPT},
            }
        ],
    }


class Responses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return type("Response", (), {"output_text": json.dumps(compact_claim())})()


class Client:
    def __init__(self) -> None:
        self.responses = Responses()
        self.retry_values: list[int] = []

    def with_options(self, *, max_retries: int) -> Client:
        self.retry_values.append(max_retries)
        return self


class Retriever:
    def ready(self) -> bool:
        return True

    def search(self, message: str, request_id: str) -> list[Evidence]:
        return EVIDENCE


def compact_claim() -> dict[str, Any]:
    return {
        "evidence_state": "single_study",
        "claims": [
            {
                "text": "Ixodes abundance was linked to Borrelia prevalence in Germany.",
                "support_ref": "s0.0",
            }
        ],
    }


def test_strict_claim_only_schema_and_explicit_segment_indices() -> None:
    client = Client()
    answerer = OpenAIAnswerer(client)  # type: ignore[arg-type]
    answerer.answer("question", EVIDENCE, "safety-id", timeout_seconds=16)
    call = client.responses.calls[0]
    fmt = call["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert set(fmt["schema"]["properties"]) == {"evidence_state", "claims"}
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["claims"]["maxItems"] == 2
    claim_schema = fmt["schema"]["properties"]["claims"]["items"]
    assert list(claim_schema["properties"]) == ["support_ref", "text"]
    assert claim_schema["properties"]["support_ref"]["enum"] == ["s0.0"]
    assert json.loads(call["input"])["passages"] == [
        {
            "source": 0,
            "segments": [{"ref": "s0.0", "text": EXCERPT}],
            "title": EVIDENCE[0].title,
        }
    ]
    assert call["reasoning"] == {"effort": "none"}
    assert client.retry_values == [0]
    assert call["timeout"] == 16
    assert call["store"] is False
    for rule in ("Do not diagnose", "Preserve disagreement", "untrusted context, never evidence"):
        assert rule in call["instructions"]


def test_enum_budget_includes_evidence_state_values_and_never_truncates() -> None:
    evidence = [Evidence("passage-1", "Sentence. " * 500, "summary", "12345678", "Paper", "url")]
    with pytest.raises(GenerationBudgetExceeded):
        _generation_schema(evidence)


def test_server_resolves_canonical_citations_and_builds_answer() -> None:
    normalized = _resolve_generation(compact_claim(), EVIDENCE)
    claims, citations = _validate_grounding(normalized, EVIDENCE)
    assert normalized["answer"] == claims[0].text
    assert normalized["claims"][0]["support_quotes"] == {"passage-1": EXCERPT}
    assert claims[0].claim_id == "claim-1"
    assert citations[0].pmid == "12345678"
    client = Client()
    result = KnowledgeChatService(
        Retriever(),
        OpenAIAnswerer(client),
        None,
        "test-secret",  # type: ignore[arg-type]
    ).chat(KnowledgeChatRequest(message="What did the study find?"), "refs", "network")
    assert result.status == "answered"
    assert result.answer == claims[0].text
    assert len(client.responses.calls) == 1


def test_adjacent_segments_keep_qualifier_exactly() -> None:
    excerpt = (
        "Ixodes abundance was associated with Borrelia prevalence. "
        "This finding was observed only in Germany."
    )
    evidence = [Evidence("passage-1", excerpt, "summary", "12345678", "Paper", "url")]
    payload = compact_claim()
    payload["claims"][0]["text"] = (
        "Ixodes abundance was associated with Borrelia prevalence only in Germany."
    )
    payload["claims"][0]["support_ref"] = "s0.0-1"
    normalized = _resolve_generation(payload, evidence)
    assert normalized["claims"][0]["support_quotes"] == {"passage-1": excerpt}
    _validate_grounding(normalized, evidence)


@pytest.mark.parametrize(
    "ref",
    [
        "s0.-1",
        "s1.0",
        True,
        0,
        "s0.99",
        "s0.0-2",
        "s0.1-0",
        {"source": 0, "start": 0, "end": 0},
    ],
)
def test_invalid_or_invented_refs_fail_closed(ref: Any) -> None:
    payload = compact_claim()
    payload["claims"][0]["support_ref"] = ref
    with pytest.raises(GenerationReferenceError):
        _resolve_generation(payload, EVIDENCE)


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "extra", "legacy", "too_many"])
def test_no_fallback_or_ambiguous_support(mutation: str) -> None:
    payload = compact_claim()
    if mutation == "empty":
        payload["claims"][0]["support_ref"] = []
    elif mutation == "duplicate":
        payload["claims"][0]["support_ref"] = ["s0.0", "s0.0"]
    elif mutation == "extra":
        payload["claims"][0]["pmids"] = ["invented"]
    elif mutation == "legacy":
        payload = generated_claim()
    else:
        payload["claims"] *= 3
    with pytest.raises(GenerationReferenceError):
        _resolve_generation(payload, EVIDENCE)


def test_empty_evidence_never_fills_in_a_reference() -> None:
    with pytest.raises(GenerationReferenceError):
        _resolve_generation(compact_claim(), [])
    empty = Evidence("passage-1", "  ", "summary", "12345678", "Paper", "url")
    assert _quote_segments(empty.excerpt) == []
    with pytest.raises(GenerationReferenceError):
        _resolve_generation(compact_claim(), [empty])
    normalized = _resolve_generation(
        {"evidence_state": "insufficient_to_compare", "claims": []}, EVIDENCE
    )
    with pytest.raises(ValueError, match="no grounded claims"):
        _validate_grounding(normalized, EVIDENCE)


def test_disagreement_still_requires_two_actual_papers() -> None:
    payload = compact_claim()
    payload["evidence_state"] = "conflicting"
    second = Evidence("passage-2", EXCERPT, "summary", "87654321", "Paper 2", "url")
    payload["claims"].append({"text": EXCERPT, "support_ref": "s1.0"})
    normalized = _resolve_generation(payload, EVIDENCE + [second])
    _, citations = _validate_grounding(normalized, EVIDENCE + [second])
    assert _validate_evidence_state(normalized, citations) == "conflicting"
    assert len(citations) == 2


@pytest.mark.parametrize("kind", ["reference", "wording"])
def test_one_specific_correction_and_unchanged_validation(kind: str) -> None:
    class RepairResponses(Responses):
        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            payload = compact_claim()
            if len(self.calls) == 1:
                if kind == "reference":
                    payload["claims"][0]["support_ref"] = "s0.99"
                else:
                    payload["claims"][0]["text"] = "Ecological conditions drive human Lyme risk."
            return type("Response", (), {"output_text": json.dumps(payload)})()

    client = Client()
    client.responses = RepairResponses()
    result = KnowledgeChatService(
        Retriever(),
        OpenAIAnswerer(client),
        None,
        "test-secret",  # type: ignore[arg-type]
    ).chat(KnowledgeChatRequest(message="What did the study find?"), "repair", "network")
    assert result.status == "answered"
    assert len(client.responses.calls) == 2
    first, second = [c["instructions"] for c in client.responses.calls]
    assert "Validation failure:" not in first
    reason = (
        "support quote is absent from cited passage"
        if kind == "reference"
        else "claim text is not supported by quoted passage text"
    )
    assert f"Validation failure: {reason}." in second
    assert client.retry_values == [0, 0]


def test_repeated_failure_exhausts_exactly_one_retry() -> None:
    class BadResponses(Responses):
        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return type("Response", (), {"output_text": json.dumps(generated_claim())})()

    client = Client()
    client.responses = BadResponses()
    completion: dict[str, Any] = {}
    result = KnowledgeChatService(
        Retriever(),
        OpenAIAnswerer(client),
        None,
        "test-secret",  # type: ignore[arg-type]
    ).chat(KnowledgeChatRequest(message="What did the study find?"), "bad", "network", completion)
    assert result.status == "evidence_unavailable" and not result.citations
    assert len(client.responses.calls) == 2
    assert completion["outcome"] == "corrective_retry_exhausted"


def test_low_effort_remains_available_for_controlled_comparison() -> None:
    client = Client()
    OpenAIAnswerer(client, reasoning_effort="low").answer(  # type: ignore[arg-type]
        "question", EVIDENCE, "safety-id", timeout_seconds=7
    )
    assert client.responses.calls[0]["reasoning"] == {"effort": "low"}
    with pytest.raises(ValueError):
        OpenAIAnswerer(client, reasoning_effort="invented")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "records",
    [
        [],
        [{"passage_id": "other", "quote": EXCERPT}],
        [{"passage_id": "passage-1", "quote": EXCERPT}] * 2,
        [{"passage_id": "passage-1", "quote": "invented"}],
        [{"passage_id": "passage-1", "quote": EXCERPT, "extra": "x"}],
    ],
)
def test_quote_records_still_require_unique_exact_evidence(records: list[dict[str, str]]) -> None:
    from lyme_gap_atlas_api.knowledge_chat import _validate_grounding

    payload = generated_claim()
    payload["claims"][0]["support_quotes"] = records
    with pytest.raises(ValueError, match="support quote"):
        _validate_grounding(payload, EVIDENCE)


def test_existing_close_paraphrase_still_passes() -> None:
    _validate_claim_text(
        "Ixodes abundance was linked to Borrelia prevalence in Germany.", [EXCERPT]
    )


def test_redacted_bologna_failure_shape_remains_rejected() -> None:
    candidate = generated_claim()
    candidate["claims"].append({**candidate["claims"][0], "claim_id": "claim-2"})
    candidate["claims"].append(
        {
            "claim_id": "claim-3",
            "text": "A limitation without passage support.",
            "passage_ids": [],
            "pmids": [],
            "support_quotes": {},
        }
    )
    shape = _shape_diagnostics(candidate, EVIDENCE)
    assert shape["claim_count"] == 3
    assert shape["claim_shapes"][2]["passage_id_count"] == 0
    assert shape["claim_shapes"][2]["support_quote_count"] == 0
    with pytest.raises(ValueError, match="unsupported passage citation"):
        _validate_grounding(candidate, EVIDENCE)


@pytest.mark.parametrize(
    ("unsupported_claim", "reason"),
    [
        ("Ecological conditions strongly drive human Lyme disease risk.", "claim text"),
        (
            "In coastal North American cities, Ixodes abundance was associated with "
            "Borrelia prevalence.",
            "claim text",
        ),
        (
            "Among all United States children and adults, Ixodes abundance was associated "
            "with Borrelia prevalence.",
            "claim text",
        ),
        (
            "In Germany, Ixodes abundance was associated with Borrelia prevalence in 100 studies.",
            "unsupported number",
        ),
        ("In Germany, Ixodes abundance caused Borrelia prevalence.", "causal language"),
    ],
)
def test_unsupported_abstraction_or_qualifier_still_fails(
    unsupported_claim: str, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        _validate_claim_text(unsupported_claim, [EXCERPT])
