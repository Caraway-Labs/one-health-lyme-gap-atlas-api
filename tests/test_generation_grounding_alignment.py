"""The generation instructions target the existing deterministic claim contract."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lyme_gap_atlas_api.knowledge_chat import (
    Evidence,
    KnowledgeChatService,
    OpenAIAnswerer,
    _resolve_quote_refs,
    _shape_diagnostics,
    _validate_claim_text,
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
        return type("Response", (), {"output_text": json.dumps(generated_claim())})()


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


def test_first_and_corrective_attempts_share_quote_first_claim_rules() -> None:
    client = Client()
    answerer = OpenAIAnswerer(client)  # type: ignore[arg-type]
    answerer.answer("question", EVIDENCE, "safety-id", timeout_seconds=16)
    answerer.answer("question", EVIDENCE, "safety-id", timeout_seconds=7, correction=True)

    first, corrective = [call["instructions"] for call in client.responses.calls]
    for instructions in (first, corrective):
        assert "first select returned passage IDs and their matching PMIDs" in instructions
        assert "then select one or two adjacent numbered quote segments" in instructions
        assert 'support_quote_refs to an object' in instructions
        assert '{"passage-1": 0}' in instructions
        assert "Every claim, including a claim about a study limitation" in instructions
        assert (
            "Never add a claim with empty passage_ids, pmids, or support_quote_refs"
            in instructions
        )
        assert "omit any finding or limitation" in instructions
        assert "Only then write one short, atomic claim" in instructions
        assert "close extractive paraphrase" in instructions
        assert "preserve the source's important scientific nouns" in instructions
        assert "Make each claim no broader than its cited quotes" in instructions
        assert "validated claims[].text are the user-visible answer units" in instructions
        assert "do not add separate broader findings" in instructions
        assert "Return at most three short claims" in instructions
        assert "each citing at most two returned passages" in instructions
        assert "Copy every passage ID and PMID exactly" in instructions
        assert "If findings conflict, use claims to show both sides" in instructions
    assert "previous candidate failed deterministic grounding" not in first
    assert "previous candidate failed deterministic grounding" in corrective
    assert client.retry_values == [0, 0]
    assert [call["timeout"] for call in client.responses.calls] == [16, 7]


def test_generation_context_keeps_grounding_fields_without_duplicate_metadata() -> None:
    client = Client()
    answerer = OpenAIAnswerer(client)  # type: ignore[arg-type]
    answerer.answer("question", EVIDENCE, "safety-id", timeout_seconds=16)
    answerer.answer("question", EVIDENCE, "safety-id", timeout_seconds=7, correction=True)

    expected = [
        {
            "passage_id": item.passage_id,
            "quote_segments": [item.excerpt],
            "pmid": item.pmid,
            "title": item.title,
        }
        for item in EVIDENCE
    ]
    for call in client.responses.calls:
        passages = json.loads(call["input"])["passages"]
        assert len(passages) == len(EVIDENCE)
        assert passages == expected


def test_quote_reference_resolves_to_exact_excerpt_and_citation() -> None:
    payload = generated_claim()
    claim = payload["claims"][0]
    del claim["support_quotes"]
    claim["support_quote_refs"] = {"passage-1": 0}
    raw_payload = json.dumps(payload)

    class RefResponses(Responses):
        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return type("Response", (), {"output_text": raw_payload})()

    client = Client()
    client.responses = RefResponses()
    normalized = OpenAIAnswerer(client).answer(  # type: ignore[arg-type]
        "question", EVIDENCE, "safety-id", timeout_seconds=16
    )
    assert normalized["claims"][0]["support_quotes"] == {"passage-1": EXCERPT}
    service = KnowledgeChatService(
        Retriever(), OpenAIAnswerer(client), None, "test-secret"  # type: ignore[arg-type]
    )
    result = service.chat(
        KnowledgeChatRequest(message="What did the study find?"), "req-refs", "network"
    )
    assert result.status == "answered"
    assert result.citations[0].pmid == "12345678"


def test_invalid_quote_reference_fails_closed() -> None:
    payload = generated_claim()
    claim = payload["claims"][0]
    del claim["support_quotes"]
    claim["support_quote_refs"] = {"passage-1": 99}

    class RefResponses(Responses):
        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return type("Response", (), {"output_text": json.dumps(payload)})()

    client = Client()
    client.responses = RefResponses()
    service = KnowledgeChatService(
        Retriever(), OpenAIAnswerer(client), None, "test-secret"  # type: ignore[arg-type]
    )
    result = service.chat(
        KnowledgeChatRequest(message="What did the study find?"), "req-bad-ref", "network"
    )
    assert result.status == "evidence_unavailable"
    assert not result.citations


def test_adjacent_quote_segments_preserve_material_qualifier() -> None:
    excerpt = (
        "Ixodes abundance was associated with Borrelia prevalence. "
        "This finding was observed only in Germany."
    )
    evidence = [Evidence("passage-1", excerpt, "summary", "12345678", "Paper", "url")]
    payload = generated_claim()
    payload["claims"][0]["text"] = (
        "Ixodes abundance was associated with Borrelia prevalence only in Germany."
    )
    del payload["claims"][0]["support_quotes"]
    payload["claims"][0]["support_quote_refs"] = {"passage-1": [0, 1]}

    normalized = _resolve_quote_refs(payload, evidence)
    assert normalized["claims"][0]["support_quotes"] == {"passage-1": excerpt}
    claims, citations = _validate_grounding(normalized, evidence)
    assert len(claims) == len(citations) == 1


@pytest.mark.parametrize("ref", [-1, True, "0", [0, 2], [1, 0], [0, 0, 0]])
def test_invalid_quote_reference_shapes_never_validate(ref: Any) -> None:
    payload = generated_claim()
    del payload["claims"][0]["support_quotes"]
    payload["claims"][0]["support_quote_refs"] = {"passage-1": ref}
    normalized = _resolve_quote_refs(payload, EVIDENCE)
    with pytest.raises(ValueError):
        _validate_grounding(normalized, EVIDENCE)


def test_model_quote_string_cannot_override_quote_reference() -> None:
    payload = generated_claim()
    payload["claims"][0]["support_quote_refs"] = {"passage-1": 0}
    normalized = _resolve_quote_refs(payload, EVIDENCE)
    with pytest.raises(ValueError):
        _validate_grounding(normalized, EVIDENCE)


def test_quote_first_candidate_answers_in_one_generation_call() -> None:
    client = Client()
    service = KnowledgeChatService(
        Retriever(), OpenAIAnswerer(client), None, "test-secret"  # type: ignore[arg-type]
    )
    result = service.chat(
        KnowledgeChatRequest(message="What did the study find?"), "req-1", "network"
    )

    assert result.status == "answered"
    assert result.answer == "Ixodes abundance was linked to Borrelia prevalence in Germany."
    assert result.evidence_state == "single_study"
    assert result.citations[0].pmid == "12345678"
    assert len(client.responses.calls) == 1


def test_exact_quote_records_from_model_answer_without_retry() -> None:
    payload = generated_claim()
    payload["claims"][0]["support_quotes"] = [
        {"passage_id": "passage-1", "quote": EXCERPT}
    ]

    class RecordResponses(Responses):
        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return type("Response", (), {"output_text": json.dumps(payload)})()

    client = Client()
    client.responses = RecordResponses()
    service = KnowledgeChatService(
        Retriever(), OpenAIAnswerer(client), None, "test-secret"  # type: ignore[arg-type]
    )
    result = service.chat(
        KnowledgeChatRequest(message="What did the study find?"), "req-2", "network"
    )

    assert result.status == "answered"
    assert result.citations[0].passage_ids == ["passage-1"]
    assert len(client.responses.calls) == 1


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
