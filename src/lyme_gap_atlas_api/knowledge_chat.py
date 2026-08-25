"""Fixed-template Neo4j retrieval and fail-closed evidence chat."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, cast

from lyme_gap_atlas_kg import CONFIGURATION_VERSION, asset_path
from lyme_gap_atlas_shared.settings import SnowflakeSettings
from lyme_gap_atlas_shared.snowflake import connect
from neo4j import GraphDatabase, Query
from openai import OpenAI

from .models import (
    KnowledgeChatRequest,
    KnowledgeChatResponse,
    KnowledgeCitation,
    KnowledgeClaim,
)

_PUBLIC_COPY = json.loads(asset_path("config", "public-copy-v1.json").read_text(encoding="utf-8"))
MEDICAL_NOTICE = str(_PUBLIC_COPY["medical_notice"])
SAFETY_REFUSAL = str(_PUBLIC_COPY["safety_refusal"])
NO_EVIDENCE = str(_PUBLIC_COPY["no_evidence"])
EVIDENCE_UNAVAILABLE = str(_PUBLIC_COPY["evidence_unavailable"])
CAPACITY_LIMITED = str(_PUBLIC_COPY["capacity_limited"])

HYBRID_SEARCH = """
CALL db.index.fulltext.queryNodes('entity_names', $query, {limit: 10}) YIELD node, score
WITH collect({node: node, score: score}) AS entities
CALL db.index.vector.queryNodes('evidence_passage_summary', 20, $embedding)
YIELD node AS passage, score AS vector_score
MATCH (paper:Paper {id: passage.paper_id})
RETURN passage.id AS passage_id, passage.excerpt AS excerpt,
       passage.extraction_summary AS summary, paper.pmid AS pmid,
       paper.title AS title, paper.pubmed_url AS pubmed_url,
       vector_score, [entity IN entities | entity.node.canonical_name][0..10] AS matched_entities
ORDER BY vector_score DESC LIMIT 20
"""


@dataclass(frozen=True)
class Evidence:
    passage_id: str
    excerpt: str
    summary: str
    pmid: str
    title: str
    pubmed_url: str


class ChatResponseBase(TypedDict):
    request_id: str
    conversation_id: str
    conversation_token: str | None
    configuration_version: str


class Retriever(Protocol):
    def ready(self) -> bool: ...

    def search(self, message: str) -> list[Evidence]: ...


class Answerer(Protocol):
    def answer(self, message: str, evidence: list[Evidence], safety_id: str) -> dict[str, Any]: ...


class BudgetStore(Protocol):
    def authorize(self, conversation_id: str, token_hash: str) -> bool: ...

    def reserve(self, request_id: str) -> bool: ...

    def persist(
        self,
        *,
        request_id: str,
        conversation_id: str,
        token_hash: str,
        network_hash: str,
        request: KnowledgeChatRequest,
        response: KnowledgeChatResponse,
    ) -> None: ...


class Neo4jRetriever:
    """Read-only retriever exposing no arbitrary-Cypher interface."""

    def __init__(self, uri: str, user: str, password: str, openai: OpenAI) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._openai = openai

    def ready(self) -> bool:
        self._driver.verify_connectivity()
        return True

    def search(self, message: str) -> list[Evidence]:
        embedding = (
            self._openai.embeddings.create(
                model="text-embedding-3-small", input=message, dimensions=1024
            )
            .data[0]
            .embedding
        )
        records, _, _ = self._driver.execute_query(
            Query(HYBRID_SEARCH, timeout=5),
            {"query": message, "embedding": embedding},
            database_="neo4j",
        )
        return [
            Evidence(
                passage_id=record["passage_id"],
                excerpt=record["excerpt"],
                summary=record["summary"],
                pmid=str(record["pmid"]),
                title=record["title"],
                pubmed_url=record["pubmed_url"],
            )
            for record in records
        ]


class SnowflakeBudgetStore:
    """Procedure-only budget reservation and 30-day conversation persistence."""

    def __init__(self, settings: SnowflakeSettings) -> None:
        self._settings = settings

    def authorize(self, conversation_id: str, token_hash: str) -> bool:
        with connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "CALL GOVERNANCE.SP_VERIFY_KG_CONVERSATION_TOKEN(%s,%s)",
                (conversation_id, token_hash),
            )
            row = cursor.fetchone()
            return bool(row and row[0])

    def reserve(self, request_id: str) -> bool:
        with connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "CALL GOVERNANCE.SP_RESERVE_KG_LLM_BUDGET(%s,%s,%s,%s,%s,%s,%s)",
                ("chat", request_id, "openai", "gpt-5.6-luna", 0.05, 5, 100),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            payload = row[0] if isinstance(row[0], dict) else json.loads(str(row[0]))
            return bool(payload["allowed"])

    def persist(
        self,
        *,
        request_id: str,
        conversation_id: str,
        token_hash: str,
        network_hash: str,
        request: KnowledgeChatRequest,
        response: KnowledgeChatResponse,
    ) -> None:
        citations = [item.model_dump(mode="json") for item in response.citations]
        turns: tuple[tuple[str, str, str, str, list[dict[str, Any]]], ...] = (
            (f"{request_id}:user", "user", request.message, "received", []),
            (request_id, "assistant", response.answer, response.status, citations),
        )
        with connect(self._settings) as connection, connection.cursor() as cursor:
            for turn_request_id, role, body, status, turn_citations in turns:
                cursor.execute(
                    "CALL GOVERNANCE.SP_PERSIST_KG_CONVERSATION_TURN"
                    "(%s,%s,%s,%s,%s,%s,%s,%s,PARSE_JSON(%s))",
                    (
                        conversation_id,
                        token_hash,
                        network_hash,
                        str(uuid.uuid4()),
                        turn_request_id,
                        role,
                        body,
                        status,
                        json.dumps(turn_citations),
                    ),
                )


class OpenAIAnswerer:
    def __init__(self, client: OpenAI, model: str = "gpt-5.6-luna") -> None:
        self._client = client
        self._model = model

    def answer(self, message: str, evidence: list[Evidence], safety_id: str) -> dict[str, Any]:
        passages = [item.__dict__ for item in evidence]
        response = self._client.responses.create(
            model=self._model,
            store=False,
            reasoning={"effort": "low"},
            safety_identifier=safety_id,
            instructions=(
                "Answer only from the supplied reviewed passages. Return JSON with answer and "
                "claims. Each claim has claim_id, text, passage_ids, and pmids. Do not diagnose "
                "or provide personalized treatment. Preserve conflicting findings."
            ),
            input=json.dumps({"question": message, "passages": passages}),
            text={"format": {"type": "json_object"}},
            timeout=10,
        )
        return cast(dict[str, Any], json.loads(response.output_text))


def _unsafe_request(message: str) -> bool:
    normalized = message.casefold()
    personal = (" i ", " my ", " me ", "my child", "should i")
    action = ("diagnos", "dose", "dosage", "prescri", "treatment plan", "stop my")
    padded = f" {normalized} "
    medical = any(term in padded for term in personal) and any(
        term in normalized for term in action
    )
    injection = any(
        term in normalized
        for term in (
            "ignore the graph",
            "ignore your instructions",
            "hidden instructions",
            "database credentials",
            "detach delete",
            "made-up pmid",
        )
    )
    return medical or injection


class KnowledgeChatService:
    def __init__(
        self,
        retriever: Retriever,
        answerer: Answerer,
        budget_store: BudgetStore | None,
        hash_secret: str,
    ) -> None:
        self._retriever = retriever
        self._answerer = answerer
        self._store = budget_store
        self._secret = hash_secret.encode()

    def _hash(self, value: str) -> str:
        return hmac.new(self._secret, value.encode(), hashlib.sha256).hexdigest()

    def ready(self) -> bool:
        return self._retriever.ready()

    def _persist(
        self,
        request: KnowledgeChatRequest,
        response: KnowledgeChatResponse,
        token: str,
        network_hash: str,
    ) -> bool:
        if self._store is None:
            return True
        self._store.persist(
            request_id=response.request_id,
            conversation_id=response.conversation_id,
            token_hash=self._hash(token),
            network_hash=network_hash,
            request=request,
            response=response,
        )
        return True

    def chat(
        self, request: KnowledgeChatRequest, request_id: str, network_identifier: str
    ) -> KnowledgeChatResponse:
        conversation_id = request.conversation_id or str(uuid.uuid4())
        token = request.conversation_token or secrets.token_urlsafe(32)
        response_token = token if request.conversation_token is None else None
        base: ChatResponseBase = {
            "request_id": request_id,
            "conversation_id": conversation_id,
            "conversation_token": response_token,
            "configuration_version": CONFIGURATION_VERSION,
        }
        if bool(request.conversation_id) != bool(request.conversation_token):
            raise ValueError("conversation_id and conversation_token must be provided together")
        if (
            request.conversation_id
            and self._store is not None
            and not self._store.authorize(conversation_id, self._hash(token))
        ):
            raise ValueError("conversation capability is invalid")
        safety_id = self._hash(network_identifier)
        if _unsafe_request(request.message):
            result = KnowledgeChatResponse(**base, status="safety_refusal", answer=SAFETY_REFUSAL)
            try:
                self._persist(request, result, token, safety_id)
                return result
            except Exception:
                return KnowledgeChatResponse(
                    **base, status="evidence_unavailable", answer=EVIDENCE_UNAVAILABLE
                )
        try:
            if not self._retriever.ready():
                raise RuntimeError("Neo4j is unavailable")
            evidence = self._retriever.search(request.message)
        except Exception:
            return KnowledgeChatResponse(
                **base, status="evidence_unavailable", answer=EVIDENCE_UNAVAILABLE
            )
        if not evidence:
            result = KnowledgeChatResponse(**base, status="no_evidence", answer=NO_EVIDENCE)
            try:
                self._persist(request, result, token, safety_id)
                return result
            except Exception:
                return KnowledgeChatResponse(
                    **base, status="evidence_unavailable", answer=EVIDENCE_UNAVAILABLE
                )
        if self._store is not None and not self._store.reserve(request_id):
            return KnowledgeChatResponse(**base, status="capacity_limited", answer=CAPACITY_LIMITED)
        try:
            last_error: Exception | None = None
            for _ in range(2):
                try:
                    generated = self._answerer.answer(request.message, evidence, safety_id)
                    claims, citations = _validate_grounding(generated, evidence)
                    break
                except Exception as exc:
                    last_error = exc
            else:
                raise last_error or ValueError("grounding failed")
            result = KnowledgeChatResponse(
                **base,
                status="answered",
                answer="\n\n".join(claim.text for claim in claims),
                claims=claims,
                citations=citations,
            )
        except Exception:
            return KnowledgeChatResponse(
                **base, status="evidence_unavailable", answer=EVIDENCE_UNAVAILABLE
            )
        if self._store is not None:
            try:
                self._persist(request, result, token, safety_id)
            except Exception:
                return KnowledgeChatResponse(
                    **base, status="evidence_unavailable", answer=EVIDENCE_UNAVAILABLE
                )
        return result


def _validate_grounding(
    generated: dict[str, Any], evidence: list[Evidence]
) -> tuple[list[KnowledgeClaim], list[KnowledgeCitation]]:
    available = {item.passage_id: item for item in evidence}
    claims: list[KnowledgeClaim] = []
    citation_map: dict[str, KnowledgeCitation] = {}
    for raw in generated.get("claims", []):
        passage_ids = list(dict.fromkeys(raw.get("passage_ids", [])))
        pmids = set(raw.get("pmids", []))
        if not passage_ids or any(item not in available for item in passage_ids):
            raise ValueError("unsupported passage citation")
        actual_pmids = {available[item].pmid for item in passage_ids}
        if pmids != actual_pmids:
            raise ValueError("invented or missing PMID")
        citation_ids: list[str] = []
        for pmid in sorted(actual_pmids):
            items = [available[item] for item in passage_ids if available[item].pmid == pmid]
            citation_id = f"pmid:{pmid}"
            citation_ids.append(citation_id)
            existing = citation_map.get(citation_id)
            claim_ids = list(
                dict.fromkeys((existing.claim_ids if existing else []) + [raw["claim_id"]])
            )
            cited_passages = list(
                dict.fromkeys(
                    (existing.passage_ids if existing else []) + [item.passage_id for item in items]
                )
            )
            citation_map[citation_id] = KnowledgeCitation(
                citation_id=citation_id,
                pmid=pmid,
                title=items[0].title,
                pubmed_url=items[0].pubmed_url,
                claim_ids=claim_ids,
                passage_ids=cited_passages,
            )
        claims.append(
            KnowledgeClaim(claim_id=raw["claim_id"], text=raw["text"], citation_ids=citation_ids)
        )
    if not claims or not str(generated.get("answer", "")).strip():
        raise ValueError("answer has no grounded claims")
    return claims, list(citation_map.values())
