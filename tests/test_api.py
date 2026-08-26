from datetime import UTC, datetime

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.knowledge_chat import Evidence, KnowledgeChatService
from lyme_gap_atlas_api.models import AtlasMetadata, CountyRecord, SourceMetadata
from lyme_gap_atlas_api.repository import Snapshot


class FakeRepository:
    def ready(self) -> bool:
        return True

    def load_snapshot(self) -> Snapshot:
        metadata = AtlasMetadata(
            release_id="alpha-2026-08-06",
            schema_version="0.2.0",
            generated_at=datetime(2026, 8, 6, tzinfo=UTC),
            loaded_at=datetime.now(UTC),
            scope="United States counties",
            bundle_sha256="a" * 64,
            score_defaults={"ecological_share": 0.65},
            methodology_version="alpha-0.2.0",
            limitations="Not individual risk.",
            sources=[
                SourceMetadata(
                    key="human",
                    label="CDC",
                    vintage="2023",
                    url="https://cdc.gov",
                    note="Published floor",
                )
            ],
            states=[{"code": "CO", "name": "Colorado"}],
        )
        county = CountyRecord(
            release_id=metadata.release_id,
            fips="08001",
            county="Adams",
            state="CO",
            state_name="Colorado",
            population=500_000,
            in_contiguous_tick_scope=True,
            human_status="no_county_linked_record",
            case_count_floor_2023=None,
            incidence_floor_2023=None,
            state_unallocated_records_2023=1,
            tick_status="Established",
            scapularis_status="Established",
            pacificus_status="No records",
            burgdorferi_status="Present",
            svi_percentile=0.5,
            uninsured_percentile=0.5,
            uninsured_percent=8.0,
            rucc_2023=2,
            evidence_completeness=6,
            geometry={"type": "Polygon", "coordinates": []},
        )
        return Snapshot(metadata=metadata, counties=[county])


def client() -> TestClient:
    settings = ApiSettings(
        snowflake_account="test",
        snowflake_user="test",
        snowflake_role="test",
        snowflake_pat="test",
        cors_origins=["https://carawaylabs.com"],
        rate_limit_per_minute=100,
    )
    return TestClient(create_app(FakeRepository(), settings))


def test_neo4j_community_runtime_identity_defaults_to_shared_graph_user() -> None:
    assert ApiSettings().neo4j_runtime_user == "graph_runtime"


class FakeRetriever:
    def __init__(self, evidence: list[Evidence]) -> None:
        self.evidence = evidence

    def ready(self) -> bool:
        return True

    def search(self, message: str) -> list[Evidence]:
        return self.evidence


class FakeAnswerer:
    def answer(self, message: str, evidence: list[Evidence], safety_id: str) -> dict[str, object]:
        return {
            "answer": "Reviewed evidence associates the vector with the pathogen.",
            "claims": [
                {
                    "claim_id": "claim-1",
                    "text": "The vector is associated with the pathogen.",
                    "passage_ids": ["passage-1"],
                    "pmids": ["12345678"],
                }
            ],
        }


class InventedCitationAnswerer:
    def __init__(self) -> None:
        self.calls = 0

    def answer(self, message: str, evidence: list[Evidence], safety_id: str) -> dict[str, object]:
        self.calls += 1
        return {
            "answer": "Unsupported answer.",
            "claims": [
                {
                    "claim_id": "bad",
                    "text": "Unsupported",
                    "passage_ids": ["invented-passage"],
                    "pmids": ["99999999"],
                }
            ],
        }


def chat_client(evidence: list[Evidence]) -> TestClient:
    settings = ApiSettings(
        snowflake_account="test",
        snowflake_user="test",
        snowflake_role="test",
        snowflake_pat="test",
        cors_origins=["https://carawaylabs.com"],
        rate_limit_per_minute=100,
        knowledge_chat_enabled=True,
    )
    service = KnowledgeChatService(FakeRetriever(evidence), FakeAnswerer(), None, "test-secret")
    return TestClient(create_app(FakeRepository(), settings, service))


def test_health_and_contract() -> None:
    api = client()
    assert api.get("/health/live").json() == {"status": "ok"}
    assert api.get("/health/ready").status_code == 200
    assert api.get("/openapi.json").status_code == 200


def test_scores_geometry_detail_and_csv() -> None:
    api = client()
    score = api.get("/v1/atlas/scores").json()["counties"][0]
    assert score["fips"] == "08001"
    assert score["score"]["human_weakness"] == 75
    assert api.get("/v1/atlas/geometry").headers["cache-control"].endswith("immutable")
    assert api.get("/v1/counties/08001").json()["release"]["sources"][0]["key"] == "human"
    assert "Adams" in api.get("/v1/atlas/ranking.csv?state=CO").text


def test_validation_and_unknown_release() -> None:
    api = client()
    invalid = api.get("/v1/atlas/scores?ecological_share=63")
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
    assert api.get("/v1/atlas/metadata?dataset_version=missing").status_code == 404


def test_comma_separated_cors_origins_work_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://carawaylabs.com,http://localhost:3000")

    settings = ApiSettings(
        snowflake_account="test",
        snowflake_user="test",
        snowflake_role="test",
        snowflake_pat="test",
    )

    assert settings.cors_origins == ["https://carawaylabs.com", "http://localhost:3000"]


def test_chat_is_grounded_and_returns_one_time_token() -> None:
    api = chat_client(
        [
            Evidence(
                passage_id="passage-1",
                excerpt="Ixodes was associated with Borrelia.",
                summary="Vector-pathogen association.",
                pmid="12345678",
                title="Vector evidence",
                pubmed_url="https://pubmed.ncbi.nlm.nih.gov/12345678/",
            )
        ]
    )
    response = api.post("/v1/knowledge-graph/chat", json={"message": "What is associated?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["conversation_token"]
    assert body["citations"][0]["pmid"] == "12345678"


def test_chat_no_evidence_never_calls_answer_model() -> None:
    response = chat_client([]).post(
        "/v1/knowledge-graph/chat", json={"message": "Evidence on Mars?"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "no_evidence"


def test_chat_refuses_personalized_medical_request() -> None:
    response = chat_client([]).post(
        "/v1/knowledge-graph/chat",
        json={"message": "Should I stop my antibiotics and what dose should I take?"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "safety_refusal"


def test_chat_history_is_bounded_and_alternating() -> None:
    response = chat_client([]).post(
        "/v1/knowledge-graph/chat",
        json={"message": "Question", "history": [{"role": "assistant", "content": "bad"}]},
    )
    assert response.status_code == 422


def test_chat_rejects_invented_citations_after_one_repair() -> None:
    evidence = [
        Evidence(
            "passage-1",
            "Excerpt",
            "Summary",
            "12345678",
            "Paper",
            "https://pubmed.ncbi.nlm.nih.gov/12345678/",
        )
    ]
    answerer = InventedCitationAnswerer()
    settings = ApiSettings(
        snowflake_account="test",
        snowflake_user="test",
        snowflake_role="test",
        snowflake_pat="test",
        knowledge_chat_enabled=True,
        rate_limit_per_minute=100,
    )
    service = KnowledgeChatService(FakeRetriever(evidence), answerer, None, "test-secret")
    response = TestClient(create_app(FakeRepository(), settings, service)).post(
        "/v1/knowledge-graph/chat", json={"message": "Invent a citation"}
    )
    assert response.status_code == 503
    assert response.json()["status"] == "evidence_unavailable"
    assert answerer.calls == 2


def test_chat_requires_conversation_capability_pair() -> None:
    response = chat_client([]).post(
        "/v1/knowledge-graph/chat",
        json={"message": "Continue", "conversation_id": "conversation-1"},
    )
    assert response.status_code == 422


def test_chat_rate_limit_is_rfc_problem() -> None:
    api = chat_client([])
    for index in range(10):
        assert (
            api.post("/v1/knowledge-graph/chat", json={"message": f"Question {index}"}).status_code
            == 200
        )
    limited = api.post("/v1/knowledge-graph/chat", json={"message": "Question 11"})
    assert limited.status_code == 429
    assert limited.headers["content-type"].startswith("application/problem+json")
    assert limited.headers["retry-after"] == "600"
