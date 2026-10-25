"""Valid claims survive a bad sibling, and duplicate passage ids cannot swap a PMID (#305)."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

import pytest
from test_knowledge_chat_latency import EVIDENCE, EXCERPT, Answerer, Clock, Store, valid_payload

from lyme_gap_atlas_api.knowledge_chat import (
    Evidence,
    KnowledgeChatService,
    _dedupe_passages,
    _grounded_claims,
    _validate_grounding,
)
from lyme_gap_atlas_api.models import KnowledgeChatRequest

BAD_TEXT = "Unrelated sentence about oceans."
REASON = "claim text is not supported by quoted passage text"


class FixedRetriever:
    def __init__(self, evidence: list[Evidence]) -> None:
        self.evidence = evidence

    def ready(self) -> bool:
        return True

    def search(self, message: str, request_id: str) -> list[Evidence]:
        return self.evidence


def claim(number: int, text: str = EXCERPT) -> dict[str, Any]:
    item = deepcopy(valid_payload()["claims"][0])
    item.update({"claim_id": f"claim-{number}", "text": text})
    return item


def payload(*claims: dict[str, Any], state: str = "single_study") -> dict[str, Any]:
    return {
        "answer": "\n\n".join(item["text"] for item in claims),
        "evidence_state": state,
        "claims": list(claims),
    }


def ask(
    results: list[dict[str, Any]],
    evidence: list[Evidence] | None = None,
    store: Store | None = None,
) -> tuple[Any, Answerer, dict[str, Any]]:
    clock = Clock()
    answerer = Answerer(results, clock)
    service = KnowledgeChatService(
        FixedRetriever(evidence or EVIDENCE), answerer, store, "test-secret", clock=clock
    )
    completion: dict[str, Any] = {}
    request = KnowledgeChatRequest(message="What did the study find?")
    response = service.chat(request, "req-salvage", "network", completion)
    return response, answerer, completion


def test_valid_claim_is_kept_when_a_sibling_fails_grounding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    store = Store()
    response, answerer, completion = ask([payload(claim(1), claim(2, BAD_TEXT))], store=store)
    assert response.status == "answered"
    assert [item.claim_id for item in response.claims] == ["claim-1"]
    assert response.answer == EXCERPT
    assert response.evidence_state == "limited"
    assert [item.pmid for item in response.citations] == ["12345678"]
    assert response.citations[0].claim_ids == ["claim-1"]
    assert len(answerer.calls) == 1
    assert store.persist_calls == 1
    assert completion["outcome"] == "answered"
    partial = [r for r in caplog.records if r.getMessage() == "knowledge_chat_grounding_partial"]
    assert len(partial) == 1
    context = partial[0].context  # type: ignore[attr-defined]
    assert context["kept_claim_count"] == 1
    assert context["dropped_claim_count"] == 1
    assert context["reasons"] == [REASON]
    assert BAD_TEXT not in caplog.text


def test_a_fully_grounded_answer_keeps_its_state_and_logs_no_partial(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    response, answerer, _ = ask([payload(claim(1))])
    assert response.status == "answered"
    assert response.evidence_state == "single_study"
    assert not [r for r in caplog.records if r.getMessage() == "knowledge_chat_grounding_partial"]
    assert len(answerer.calls) == 1


def test_all_invalid_claims_still_retry_once_and_fail_closed() -> None:
    bad = payload(claim(1, BAD_TEXT), claim(2, BAD_TEXT))
    response, answerer, completion = ask([bad, bad])
    assert response.status == "evidence_unavailable"
    assert len(answerer.calls) == 2
    assert completion["outcome"] == "corrective_retry_exhausted"
    assert completion["validation_outcome"] == REASON


def test_a_dropped_claim_cannot_launder_an_invalid_evidence_state() -> None:
    bad = payload(claim(1), claim(2, BAD_TEXT), state="bogus")
    response, answerer, completion = ask([bad, bad])
    assert response.status == "evidence_unavailable"
    assert completion["validation_outcome"] == "invalid evidence state"
    assert len(answerer.calls) == 2


def test_duplicate_passage_ids_cannot_swap_the_cited_pmid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    collision = Evidence(
        "passage-1", "A different paper text.", "Other", "99999999", "Other paper", "https://x.test/9"
    )
    response, answerer, _ = ask([payload(claim(1))], evidence=[*EVIDENCE, collision])
    assert response.status == "answered"
    assert [item.pmid for item in response.citations] == ["12345678"]
    assert len(answerer.calls) == 1
    duplicates = [
        r for r in caplog.records if r.getMessage() == "knowledge_chat_duplicate_passage_ids"
    ]
    assert duplicates[0].context["duplicate_passage_count"] == 1  # type: ignore[attr-defined]


def test_dedupe_keeps_the_first_ranked_row() -> None:
    first = EVIDENCE[0]
    other = Evidence("passage-2", "Second.", "S", "222", "T2", "https://x.test/2")
    again = Evidence("passage-1", "Again.", "S", "333", "T3", "https://x.test/3")
    unique, removed = _dedupe_passages([first, other, again])
    assert unique == [first, other]
    assert removed == 1


def test_claims_on_the_same_paper_merge_into_one_citation() -> None:
    claims, citations, dropped = _grounded_claims(payload(claim(1), claim(2)), EVIDENCE)
    assert dropped == []
    assert [item.claim_id for item in claims] == ["claim-1", "claim-2"]
    assert len(citations) == 1
    assert citations[0].claim_ids == ["claim-1", "claim-2"]
    assert citations[0].passage_ids == ["passage-1"]


def test_strict_validation_still_raises_the_first_failure() -> None:
    with pytest.raises(ValueError, match="claim text is not supported"):
        _validate_grounding(payload(claim(1, BAD_TEXT), claim(2)), EVIDENCE)
    with pytest.raises(ValueError, match="no grounded claims"):
        _validate_grounding({"answer": "", "evidence_state": "limited", "claims": []}, EVIDENCE)
