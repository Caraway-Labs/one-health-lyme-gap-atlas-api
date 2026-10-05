"""Composition retains branch provenance and refuses unsupported comparisons."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_ask_atlas_orchestration import FakeTools
from test_ask_atlas_tools import tools as real_tools
from test_knowledge_chat_latency import Answerer, Clock, Retriever, Store, valid_payload

from lyme_gap_atlas_api import app as application
from lyme_gap_atlas_api import knowledge_chat as knowledge_chat_module
from lyme_gap_atlas_api import middleware as middleware_module
from lyme_gap_atlas_api.ask_atlas_mixed_composition import (
    MixedAssistantResponse,
    compose_results,
)
from lyme_gap_atlas_api.ask_atlas_mixed_service import MixedAssistant
from lyme_gap_atlas_api.ask_atlas_orchestration import (
    StructuredAssistant,
    StructuredAssistantRequest,
)
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.knowledge_chat import KnowledgeChatService
from lyme_gap_atlas_api.models import KnowledgeChatResponse
from lyme_gap_atlas_api.telemetry_logging import completion_context


def _structured():  # type: ignore[no-untyped-def]
    return StructuredAssistant(FakeTools()).ask(
        StructuredAssistantRequest(
            question="What is the 2023 Lyme case count for county 08001?",
            context={
                "measure_id": "case_count_floor_2023",
                "geography_ids": ["08001"],
                "year": 2023,
            },
        )
    )


def _literature() -> KnowledgeChatResponse:
    return KnowledgeChatResponse(
        request_id="fixture-request",
        conversation_id="fixture-conversation",
        configuration_version="kg-v1.0.0",
        assistant_policy_version="assistant-policy-v1",
        status="answered",
        answer="A cited study reports a Lyme association.",
        evidence_state="single_study",
        source_used="literature_evidence",
        model_id="fixture-model",
        claims=[
            {
                "claim_id": "lit-claim-1",
                "text": "A cited study reports a Lyme association.",
                "citation_ids": ["pmid:12345"],
            }
        ],
        citations=[
            {
                "citation_id": "pmid:12345",
                "pmid": "12345",
                "title": "Fixture study",
                "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
                "claim_ids": ["lit-claim-1"],
                "passage_ids": ["passage:12345:1"],
                "pmcid": "PMC12345",
            }
        ],
    )


def test_two_grounded_branches_remain_distinct_and_incomparable() -> None:
    structured = _structured()
    literature = _literature()
    assert structured.answer.outcome == "ANSWERED"
    result = compose_results(
        "Both", structured=structured, literature=literature,
        structured_requested=True, literature_requested=True,
    )
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.cross_source_state == "insufficient_to_compare"
    assert result.actual_sources_used == ("structured_atlas", "literature_evidence")
    assert result.structured is not None and result.literature is not None
    assert result.structured.answer.claims == structured.answer.claims
    assert result.literature.claims == literature.claims
    assert result.literature.citations == literature.citations
    assert result.structured.answer.replay.release_id
    assert result.literature.configuration_version == "kg-v1.0.0"


def test_one_dependency_failure_preserves_other_grounded_branch() -> None:
    result = compose_results(
        "Both", structured=_structured(), structured_requested=True,
        literature_requested=True,
    )
    assert result.outcome == "ANSWERED"
    assert result.actual_sources_used == ("structured_atlas",)
    assert result.cross_source_state is None
    assert result.literature is None
    assert any("literature" in item for item in result.limitations)


def test_bad_literature_citation_identity_is_not_admitted() -> None:
    malformed = _literature()
    malformed.citations[0].claim_ids = ["other-claim"]
    result = compose_results(
        "Both", structured=_structured(), literature=malformed,
        structured_requested=True, literature_requested=True,
    )
    assert result.outcome == "ANSWERED"
    assert result.actual_sources_used == ("structured_atlas",)
    assert result.literature is None


def test_response_validator_rejects_false_source_badge() -> None:
    result = compose_results(
        "Both", structured=_structured(), structured_requested=True,
        literature_requested=True,
    )
    payload = MixedAssistantResponse.from_composition(result).model_dump(mode="json")
    payload["actual_sources_used"] = ["structured_atlas", "literature_evidence"]
    with pytest.raises(ValidationError, match="source-used"):
        MixedAssistantResponse.model_validate(payload)


def test_response_validator_rejects_unverified_comparison_claim() -> None:
    result = compose_results(
        "Both", structured=_structured(), literature=_literature(),
        structured_requested=True, literature_requested=True,
    )
    payload = MixedAssistantResponse.from_composition(result).model_dump(mode="json")
    payload["outcome"] = "ANSWERED"
    with pytest.raises(ValidationError, match="unverified cross-source"):
        MixedAssistantResponse.model_validate(payload)


def test_literature_safety_refusal_suppresses_other_branch() -> None:
    refusal = _literature().model_copy(
        update={"status": "safety_refusal", "evidence_state": "not_applicable",
                "claims": [], "citations": []}
    )
    result = compose_results(
        "Both", structured=_structured(), literature=refusal,
        structured_requested=True, literature_requested=True,
    )
    assert result.outcome == "SAFETY_REFUSAL"
    assert result.actual_sources_used == ()
    assert result.structured is None
    assert result.literature is None


def test_duplicate_literature_claim_identity_is_not_admitted() -> None:
    duplicate = _literature()
    duplicate.claims.append(duplicate.claims[0].model_copy())
    result = compose_results(
        "Both", structured=_structured(), literature=duplicate,
        structured_requested=True, literature_requested=True,
    )
    assert result.actual_sources_used == ("structured_atlas",)
    assert result.literature is None


def test_literature_conflict_does_not_imply_cross_source_discordance() -> None:
    conflicting = _literature().model_copy(update={"evidence_state": "conflicting"})
    result = compose_results(
        "Both", structured=_structured(), literature=conflicting,
        structured_requested=True, literature_requested=True,
    )
    assert result.literature is not None
    assert result.literature.evidence_state == "conflicting"
    assert result.cross_source_state == "insufficient_to_compare"


def test_both_mode_literature_only_question_uses_only_literature() -> None:
    class LiteratureFixture:
        def chat(
            self, request: Any, request_id: str, network_identifier: str
        ) -> KnowledgeChatResponse:
            return _literature()

    result = MixedAssistant(None, LiteratureFixture()).ask(
        "What does the governed literature say about Lyme disease?",
        "Both",
        StructuredAssistantRequest(question="Question").context,
        "fixture-request",
        "fixture-client",
    )
    assert result.outcome == "ANSWERED"
    assert result.actual_sources_used == ("literature_evidence",)
    assert result.cross_source_state is None


def test_no_relevant_literature_is_not_evidence_of_absence() -> None:
    empty = _literature().model_copy(
        update={
            "status": "no_evidence", "answer": "No admitted evidence.",
            "evidence_state": "no_relevant_corpus_evidence", "claims": [], "citations": [],
        }
    )
    result = compose_results(
        "Both", structured=_structured(), literature=empty,
        structured_requested=True, literature_requested=True,
    )
    assert result.actual_sources_used == ("structured_atlas",)
    assert result.cross_source_state is None
    assert any("not evidence of absence" in item for item in result.limitations)


def test_mixed_http_reuses_both_in_process_boundaries(monkeypatch: Any) -> None:
    tools = FakeTools()
    calls: list[str] = []

    class LiteratureFixture:
        def chat(
            self, request: Any, request_id: str, network_identifier: str
        ) -> KnowledgeChatResponse:
            calls.append(request.message)
            return _literature()

    monkeypatch.setattr(application, "StructuredTools", lambda *args: tools)
    app = application.create_app(
        settings=ApiSettings(knowledge_chat_enabled=True),
        knowledge_chat_service=LiteratureFixture(),  # type: ignore[arg-type]
    )
    response = TestClient(app).post(
        "/v1/assistant/mixed",
        json={
            "question": (
                "What is the 2023 Lyme case count for county 08001? "
                "What does the governed literature say about Lyme disease?"
            ),
            "source_mode": "Both",
            "context": {
                "measure_id": "case_count_floor_2023",
                "geography_ids": ["08001"],
                "year": 2023,
            },
        },
    )
    assert response.status_code == 200
    answer = response.json()
    assert answer["actual_sources_used"] == ["structured_atlas", "literature_evidence"]
    assert answer["cross_source_state"] == "insufficient_to_compare"
    assert answer["structured"]["answer"]["claims"][0]["structured_refs"]
    assert answer["literature"]["claims"][0]["citation_ids"] == ["pmid:12345"]
    assert answer["literature"]["citations"][0]["passage_ids"] == ["passage:12345:1"]
    assert len(calls) == 1
    assert [call["tool"] for call in tools.calls] == ["get_observations"]


def test_literature_unavailable_http_is_typed_503() -> None:
    app = application.create_app(settings=ApiSettings(knowledge_chat_enabled=False))
    response = TestClient(app).post(
        "/v1/assistant/mixed",
        json={"question": "What does the governed literature say about Lyme disease?",
              "source_mode": "Literature"},
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert response.json()["outcome"] == "SOURCE_UNAVAILABLE"
    assert response.json()["actual_sources_used"] == []


@pytest.mark.parametrize("failure", ["retrieval", "capacity"])
def test_real_literature_service_typed_failure_is_503_and_single_completion(
    failure: str, monkeypatch: Any
) -> None:
    completions: list[dict[str, Any]] = []

    def capture(logger: Any, event: str, context: dict[str, Any]) -> None:
        if event == "knowledge_chat_total":
            completions.append(completion_context(event, context))

    monkeypatch.setattr(knowledge_chat_module, "emit_completion", capture)
    monkeypatch.setattr(middleware_module, "emit_completion", capture)
    class FailingRetriever(Retriever):
        def search(self, message: str, request_id: str) -> Any:
            raise RuntimeError("fixture dependency unavailable")

    class NoCapacity(Store):
        def reserve(self, request_id: str) -> bool:
            return False

    clock = Clock()
    service = KnowledgeChatService(
        FailingRetriever() if failure == "retrieval" else Retriever(),
        Answerer([valid_payload()], clock),
        NoCapacity() if failure == "capacity" else None,
        "fixture-secret", deadline_seconds=24, clock=clock,
    )
    app = application.create_app(
        settings=ApiSettings(knowledge_chat_enabled=True), knowledge_chat_service=service,
    )
    response = TestClient(app).post(
        "/v1/assistant/mixed",
        json={"question": "What does the governed literature say about Lyme disease?",
              "source_mode": "Literature"},
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert response.json()["outcome"] == "SOURCE_UNAVAILABLE"
    assert response.json()["literature"]["status"] == (
        "evidence_unavailable" if failure == "retrieval" else "capacity_limited"
    )
    assert len(completions) == 1
    assert completions[0]["outcome"] == "source_unavailable"
    assert completions[0]["path"] == "/v1/assistant/mixed"


def test_real_structured_service_typed_outage_is_source_unavailable() -> None:
    tools = FakeTools()
    tools.fail_on = "get_observations"
    structured = StructuredAssistant(tools).ask(StructuredAssistantRequest(
        question="What is the 2023 Lyme case count for county 08001?",
        context={"measure_id": "case_count_floor_2023", "geography_ids": ["08001"],
                 "year": 2023},
    ))
    assert structured.answer.outcome == "SOURCE_UNAVAILABLE"
    result = compose_results("Structured", structured=structured, structured_requested=True)
    assert result.outcome == "SOURCE_UNAVAILABLE"
    assert result.actual_sources_used == ()
    partial = compose_results(
        "Both", structured=structured, literature=_literature(),
        structured_requested=True, literature_requested=True,
    )
    assert partial.outcome == "ANSWERED"
    assert partial.actual_sources_used == ("literature_evidence",)
    assert partial.structured is not None
    assert partial.structured.answer.outcome == "SOURCE_UNAVAILABLE"


def test_real_grounded_literature_http_emits_one_accurate_completion(monkeypatch: Any) -> None:
    completions: list[dict[str, Any]] = []

    def capture(logger: Any, event: str, context: dict[str, Any]) -> None:
        if event == "knowledge_chat_total":
            completions.append(completion_context(event, context))

    monkeypatch.setattr(knowledge_chat_module, "emit_completion", capture)
    monkeypatch.setattr(middleware_module, "emit_completion", capture)
    clock = Clock()
    service = KnowledgeChatService(
        Retriever(), Answerer([valid_payload()], clock), None, "fixture-secret",
        deadline_seconds=24, clock=clock,
    )
    app = application.create_app(
        settings=ApiSettings(knowledge_chat_enabled=True), knowledge_chat_service=service,
    )
    response = TestClient(app).post(
        "/v1/assistant/mixed",
        json={"question": "What does the governed literature say about Lyme disease?",
              "source_mode": "Literature"},
    )
    assert response.status_code == 200
    assert response.json()["actual_sources_used"] == ["literature_evidence"]
    assert len(completions) == 1
    assert completions[0]["outcome"] == "answered"
    assert completions[0]["path"] == "/v1/assistant/mixed"


@pytest.mark.parametrize("question", [
    "What do published studies say about antibiotic treatment outcomes for Lyme disease?",
    "What does the literature report about Lyme diagnosis test accuracy?",
])
def test_nonpersonal_research_reaches_real_literature_service(question: str) -> None:
    class EmptyRetriever(Retriever):
        def search(self, message: str, request_id: str) -> Any:
            return []

    clock = Clock()
    service = KnowledgeChatService(
        EmptyRetriever(), Answerer([], clock), None, "fixture-secret",
        deadline_seconds=24, clock=clock,
    )
    result = MixedAssistant(None, service).ask(
        question, "Literature", StructuredAssistantRequest(question="Question").context,
        "fixture-request", "fixture-client",
    )
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.literature is not None
    assert result.literature.status == "no_evidence"


def test_mixed_route_shares_bounded_chat_rate_limit() -> None:
    app = application.create_app(settings=ApiSettings(knowledge_chat_enabled=False))
    client = TestClient(app)
    payload = {
        "question": "What does the governed literature say about Lyme disease?",
        "source_mode": "Literature",
    }
    for _ in range(10):
        assert client.post("/v1/assistant/mixed", json=payload).status_code == 503
    assert client.post("/v1/assistant/mixed", json=payload).status_code == 429


def test_unsafe_mixed_question_runs_neither_branch() -> None:
    class FailIfCalled:
        def ask(self, request: Any) -> Any:
            raise AssertionError("structured branch ran")

        def chat(self, request: Any, request_id: str, network_identifier: str) -> Any:
            raise AssertionError("literature branch ran")

    guard = FailIfCalled()
    result = MixedAssistant(guard, guard).ask(
        "Run SQL for county 01005 and tell me the Lyme risk",
        "Both",
        StructuredAssistantRequest(question="Question").context,
        "fixture-request",
        "fixture-client",
    )
    assert result.outcome == "SAFETY_REFUSAL"
    assert result.actual_sources_used == ()


def test_branch_exceptions_are_bounded_and_independent() -> None:
    class FailingStructured:
        def ask(self, request: Any) -> Any:
            raise RuntimeError("structured unavailable")

    class FailingLiterature:
        def chat(self, request: Any, request_id: str, network_identifier: str) -> Any:
            raise RuntimeError("literature unavailable")

    question = (
        "What is the 2023 Lyme case count for county 01005? "
        "What does the governed literature say about Lyme disease?"
    )
    context = StructuredAssistantRequest(question="Question").context
    result = MixedAssistant(FailingStructured(), FailingLiterature()).ask(
        question, "Both", context, "fixture-request", "fixture-client"
    )
    assert result.outcome == "SOURCE_UNAVAILABLE"
    assert result.actual_sources_used == ()
    assert result.structured is None and result.literature is None

    class AnsweredLiterature:
        def chat(
            self, request: Any, request_id: str, network_identifier: str
        ) -> KnowledgeChatResponse:
            return _literature()

    partial = MixedAssistant(FailingStructured(), AnsweredLiterature()).ask(
        question, "Both", context, "fixture-request", "fixture-client"
    )
    assert partial.outcome == "ANSWERED"
    assert partial.actual_sources_used == ("literature_evidence",)
    assert partial.cross_source_state is None


def test_mixed_service_uses_real_structured_adapter_fixture() -> None:
    adapter, _ = real_tools()

    class LiteratureFixture:
        def chat(
            self, request: Any, request_id: str, network_identifier: str
        ) -> KnowledgeChatResponse:
            return _literature()

    result = MixedAssistant(StructuredAssistant(adapter), LiteratureFixture()).ask(
        "What is the 2023 Lyme case count for county 01005? "
        "What does the governed literature say about Lyme disease?",
        "Both",
        StructuredAssistantRequest(
            question="What is the 2023 Lyme case count for county 01005?",
            context={
                "measure_id": "case_count_floor_2023",
                "geography_ids": ["01005"],
                "year": 2023,
            },
        ).context,
        "fixture-request",
        "fixture-client",
    )
    assert result.actual_sources_used == ("structured_atlas", "literature_evidence")
    assert result.structured is not None
    assert result.structured.tool_evidence[0].observations[0].value == 12
    assert result.structured.answer.replay.release_id == "release-1"


def test_both_mode_runs_existing_grounded_literature_service_with_real_structured_adapter() -> None:
    adapter, _ = real_tools()
    clock = Clock()
    literature = KnowledgeChatService(
        Retriever(), Answerer([valid_payload()], clock), None,
        "fixture-secret", deadline_seconds=24, clock=clock,
    )
    result = MixedAssistant(StructuredAssistant(adapter), literature).ask(
        "What is the 2023 Lyme case count for county 01005? "
        "What does the governed literature say about Lyme disease?",
        "Both",
        StructuredAssistantRequest(
            question="What is the 2023 Lyme case count for county 01005?",
            context={
                "measure_id": "case_count_floor_2023",
                "geography_ids": ["01005"],
                "year": 2023,
            },
        ).context,
        "fixture-request",
        "fixture-client",
    )
    assert result.actual_sources_used == ("structured_atlas", "literature_evidence")
    assert result.cross_source_state == "insufficient_to_compare"
    assert result.structured is not None and result.literature is not None
    assert result.structured.tool_evidence[0].observations[0].value == 12
    assert result.literature.citations[0].pmid == "12345678"
    assert result.literature.citations[0].passage_ids == ["passage-1"]
    assert "Germany" in result.literature.claims[0].text


def test_literature_retrieval_failure_keeps_real_structured_adapter_answer() -> None:
    adapter, _ = real_tools()

    class FailingRetriever(Retriever):
        def search(self, message: str, request_id: str) -> Any:
            raise RuntimeError("fixture dependency unavailable")

    clock = Clock()
    literature = KnowledgeChatService(
        FailingRetriever(), Answerer([valid_payload()], clock), None,
        "fixture-secret", deadline_seconds=24, clock=clock,
    )
    result = MixedAssistant(StructuredAssistant(adapter), literature).ask(
        "What is the 2023 Lyme case count for county 01005? "
        "What does the governed literature say about Lyme disease?",
        "Both",
        StructuredAssistantRequest(
            question="What is the 2023 Lyme case count for county 01005?",
            context={
                "measure_id": "case_count_floor_2023",
                "geography_ids": ["01005"],
                "year": 2023,
            },
        ).context,
        "fixture-request",
        "fixture-client",
    )
    assert result.outcome == "ANSWERED"
    assert result.actual_sources_used == ("structured_atlas",)
    assert result.literature is not None
    assert result.literature.status == "evidence_unavailable"
    assert result.cross_source_state is None
    assert any("literature" in item for item in result.limitations)


@pytest.mark.parametrize(
    ("county", "expected_sources", "expected_value_state"),
    [
        ("01003", ("structured_atlas", "literature_evidence"), "ZERO"),
        ("01001", ("literature_evidence",), None),
    ],
)
def test_missing_row_is_not_promoted_to_zero_by_literature(
    county: str, expected_sources: tuple[str, ...], expected_value_state: str | None
) -> None:
    adapter, _ = real_tools()

    class LiteratureFixture:
        def chat(
            self, request: Any, request_id: str, network_identifier: str
        ) -> KnowledgeChatResponse:
            return _literature()

    result = MixedAssistant(StructuredAssistant(adapter), LiteratureFixture()).ask(
        f"What is the 2023 Lyme case count for county {county}? "
        "What does the governed literature say about Lyme disease?",
        "Both",
        StructuredAssistantRequest(
            question=f"What is the 2023 Lyme case count for county {county}?",
            context={
                "measure_id": "case_count_floor_2023",
                "geography_ids": [county],
                "year": 2023,
            },
        ).context,
        "fixture-request",
        "fixture-client",
    )
    assert result.actual_sources_used == expected_sources
    assert result.structured is not None
    if expected_value_state is None:
        assert result.structured.answer.claims == []
        assert result.cross_source_state is None
    else:
        observation = result.structured.tool_evidence[0].observations[0]
        assert observation.value_state == expected_value_state
        assert result.cross_source_state == "insufficient_to_compare"
