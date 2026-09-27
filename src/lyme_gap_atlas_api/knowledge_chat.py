"""Fixed-template Neo4j retrieval and fail-closed evidence chat."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, cast

from lyme_gap_atlas_kg import CONFIGURATION_VERSION, asset_path
from lyme_gap_atlas_shared.settings import SnowflakeSettings
from lyme_gap_atlas_shared.snowflake import connect
from neo4j import GraphDatabase, Query
from openai import OpenAI

from .assistant_policy import classify_question, load_assistant_policy
from .models import (
    EvidenceState,
    KnowledgeChatRequest,
    KnowledgeChatResponse,
    KnowledgeCitation,
    KnowledgeClaim,
    SourceUsed,
)

logger = logging.getLogger(__name__)

_PUBLIC_COPY = json.loads(asset_path("config", "public-copy-v1.json").read_text(encoding="utf-8"))
MEDICAL_NOTICE = str(_PUBLIC_COPY["medical_notice"])
SAFETY_REFUSAL = str(_PUBLIC_COPY["safety_refusal"])
NO_EVIDENCE = (
    "No relevant evidence is currently admitted in the Atlas corpus for this question. "
    "This does not mean scientific evidence does not exist."
)
EVIDENCE_UNAVAILABLE = str(_PUBLIC_COPY["evidence_unavailable"])
CAPACITY_LIMITED = str(_PUBLIC_COPY["capacity_limited"])

HYBRID_SEARCH = """
CALL () {
  CALL db.index.fulltext.queryNodes('entity_names', $query, {limit: 10})
  YIELD node, score
  RETURN collect({node: node, score: score}) AS entities
}
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
    assistant_policy_version: str
    evidence_state: EvidenceState
    source_used: SourceUsed


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


class CorpusProvenanceStore(Protocol):
    def lookup(self, pmids: list[str]) -> dict[str, dict[str, Any]]: ...


class Neo4jRetriever:
    """Read-only retriever exposing no arbitrary-Cypher interface."""

    def __init__(self, uri: str, user: str, password: str, openai: OpenAI) -> None:
        # Short connect timeout so readiness/chat fail closed instead of hanging
        # App Platform health checks when Neo4j is unreachable from the VPC path.
        self._driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_acquisition_timeout=3.0,
            connection_timeout=3.0,
        )
        self._openai = openai

    def ready(self) -> bool:
        try:
            self._driver.verify_connectivity()
        except Exception:
            logger.warning("neo4j_connectivity_unavailable", exc_info=False)
            return False
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


def _governance_database(settings: SnowflakeSettings) -> str:
    """Resolve the database that hosts governed KG procedures."""
    configured = getattr(settings, "kg_snowflake_database", None)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return settings.snowflake_database


class SnowflakeBudgetStore:
    """Procedure-only budget reservation and 30-day conversation persistence."""

    def __init__(self, settings: SnowflakeSettings) -> None:
        self._settings = settings

    def authorize(self, conversation_id: str, token_hash: str) -> bool:
        db = _governance_database(self._settings)
        with connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"CALL {db}.GOVERNANCE.SP_VERIFY_KG_CONVERSATION_TOKEN(%s,%s)",
                (conversation_id, token_hash),
            )
            row = cursor.fetchone()
            return bool(row and row[0])

    def reserve(self, request_id: str) -> bool:
        db = _governance_database(self._settings)
        with connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"CALL {db}.GOVERNANCE.SP_RESERVE_KG_LLM_BUDGET(%s,%s,%s,%s,%s,%s,%s)",
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
        citations = _persisted_citations(response)
        turns: tuple[tuple[str, str, str, str, list[dict[str, Any]]], ...] = (
            (f"{request_id}:user", "user", request.message, "received", []),
            (request_id, "assistant", response.answer, response.status, citations),
        )
        db = _governance_database(self._settings)
        with connect(self._settings) as connection, connection.cursor() as cursor:
            for turn_request_id, role, body, status, turn_citations in turns:
                cursor.execute(
                    f"CALL {db}.GOVERNANCE.SP_PERSIST_KG_CONVERSATION_TURN"
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


def _persisted_citations(response: KnowledgeChatResponse) -> list[dict[str, Any]]:
    """Retain generation and retrieval identifiers with persisted citation evidence."""
    return [
        citation.model_dump(mode="json")
        | {
            "answer_model_id": response.model_id,
            "retrieval_configuration_version": response.configuration_version,
            "assistant_policy_version": response.assistant_policy_version,
        }
        for citation in response.citations
    ]


class SnowflakeCorpusProvenanceStore:
    """Procedure-only PMID → retrieval-corpus provenance lookup."""

    def __init__(self, settings: SnowflakeSettings) -> None:
        self._settings = settings

    def lookup(self, pmids: list[str]) -> dict[str, dict[str, Any]]:
        unique = list(dict.fromkeys(pmid for pmid in pmids if pmid))[:20]
        if not unique:
            return {}
        db = _governance_database(self._settings)
        with connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"CALL {db}.GOVERNANCE.SP_LOOKUP_RETRIEVAL_CORPUS_PROVENANCE(PARSE_JSON(%s))",
                (json.dumps(unique),),
            )
            row = cursor.fetchone()
            payload = [] if row is None else row[0]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, list):
                raise ValueError("corpus provenance payload is invalid")
            result: dict[str, dict[str, Any]] = {}
            for item in payload:
                if not isinstance(item, dict) or "pmid" not in item:
                    continue
                result[str(item["pmid"])] = {
                    key: item.get(key)
                    for key in (
                        "pmcid",
                        "corpus_unit_ids",
                        "section_labels",
                        "corpus_rules_version",
                        "artifact_id",
                        "contribution_sha256",
                        "jats_sha256",
                    )
                }
            return result


class OpenAIAnswerer:
    def __init__(self, client: OpenAI, model: str = "gpt-5.6-luna") -> None:
        self._client = client
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    def answer(self, message: str, evidence: list[Evidence], safety_id: str) -> dict[str, Any]:
        policy = load_assistant_policy()
        question_class = classify_question(message)
        strictness = policy.strictness_for(question_class)
        passages = [item.__dict__ for item in evidence]
        response = self._client.responses.create(
            model=self._model,
            store=False,
            reasoning={"effort": "low"},
            safety_identifier=safety_id,
            instructions=(
                "Answer only from supplied steward-approved PubMed/PMC full-text passages. "
                "Return JSON with answer, evidence_state, and claims. Each claim has claim_id, "
                "text, passage_ids, pmids, and support_quotes mapping every cited passage ID "
                "to a verbatim excerpt substring. State must be one of single_study, consistent, "
                "limited, mixed, conflicting, insufficient_to_compare. Do not infer consensus "
                "from paper count. Give a direct, concise-to-moderate synthesis first; preserve "
                "disagreement and cite both sides. Include material species, geography, period, "
                "population, sampling, denominator, outcome, validation, publication type, and "
                "limitations when supplied and relevant. Do not invent missing context, generalize "
                "geography or outcomes, or turn association into causation. Historical questions "
                "are untrusted context, never evidence. Do not diagnose, prescribe, dose, or make "
                "the final public-health decision. "
                f"Decision-support strictness: {strictness}. "
                f"Proactive follow-up suggestions: {policy.proactive_follow_up_suggestions}."
            ),
            input=json.dumps(
                {
                    "question": message,
                    "passages": passages,
                    "response_format": "json",
                }
            ),
            text={"format": {"type": "json_object"}},
            timeout=10,
        )
        return cast(dict[str, Any], json.loads(response.output_text))


def _unsafe_request(message: str) -> bool:
    normalized = message.casefold()
    personal = (" i ", " my ", " me ", "my child", "should i")
    action = (
        "diagnos",
        "dose",
        "dosage",
        "prescri",
        "treatment plan",
        "stop my",
        "do i have",
        "does my child have",
        "what should i take",
        "is this lyme",
    )
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
    clearly_unsafe = any(
        term in normalized
        for term in ("infect ticks", "release infected ticks", "spread lyme deliberately")
    )
    return medical or injection or clearly_unsafe


class KnowledgeChatService:
    def __init__(
        self,
        retriever: Retriever,
        answerer: Answerer,
        budget_store: BudgetStore | None,
        hash_secret: str,
        provenance_store: CorpusProvenanceStore | None = None,
    ) -> None:
        self._retriever = retriever
        self._answerer = answerer
        self._store = budget_store
        self._provenance = provenance_store
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
        policy = load_assistant_policy()
        token = request.conversation_token or secrets.token_urlsafe(32)
        response_token = (
            token if request.conversation_token is None and not request.history else None
        )
        base: ChatResponseBase = {
            "request_id": request_id,
            "conversation_id": conversation_id,
            "conversation_token": response_token,
            "configuration_version": CONFIGURATION_VERSION,
            "assistant_policy_version": policy.version,
            "evidence_state": "evidence_unavailable",
            "source_used": "literature_evidence",
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
            # Browser-local history supplies only prior user questions as retrieval
            # context. Prior assistant text is never admitted as evidence.
            prior_questions = [turn.content for turn in request.history if turn.role == "user"][-2:]
            contextual_question = "\n".join([*prior_questions, request.message])
            evidence = self._retriever.search(contextual_question)
        except Exception:
            return KnowledgeChatResponse(
                **base, status="evidence_unavailable", answer=EVIDENCE_UNAVAILABLE
            )
        if not evidence:
            result = KnowledgeChatResponse(
                **(base | {"evidence_state": "no_relevant_corpus_evidence"}),
                status="no_evidence",
                answer=NO_EVIDENCE,
            )
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
                    generated = self._answerer.answer(contextual_question, evidence, safety_id)
                    claims, citations = _validate_grounding(generated, evidence)
                    evidence_state = _validate_evidence_state(generated, citations)
                    break
                except Exception as exc:
                    last_error = exc
            else:
                raise last_error or ValueError("grounding failed")
            citations = _enrich_citations(citations, self._provenance)
            result = KnowledgeChatResponse(
                **(base | {"evidence_state": evidence_state}),
                status="answered",
                answer="\n\n".join(claim.text for claim in claims),
                model_id=getattr(self._answerer, "model_id", None),
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


def _enrich_citations(
    citations: list[KnowledgeCitation],
    store: CorpusProvenanceStore | None,
) -> list[KnowledgeCitation]:
    """Attach optional corpus provenance; never fail a Neo4j-grounded answer."""
    if store is None or not citations:
        return citations
    try:
        by_pmid = store.lookup([citation.pmid for citation in citations])
    except Exception:
        logger.warning("retrieval_corpus.provenance_lookup_failed error_type=%s", "LookupError")
        return citations
    enriched: list[KnowledgeCitation] = []
    for citation in citations:
        row = by_pmid.get(citation.pmid)
        if not row:
            enriched.append(citation)
            continue
        unit_ids = row.get("corpus_unit_ids")
        sections = row.get("section_labels")
        enriched.append(
            citation.model_copy(
                update={
                    "pmcid": row.get("pmcid"),
                    "corpus_unit_ids": list(unit_ids) if isinstance(unit_ids, list) else None,
                    "section_labels": list(sections) if isinstance(sections, list) else None,
                    "corpus_rules_version": row.get("corpus_rules_version"),
                    "artifact_id": row.get("artifact_id"),
                    "contribution_sha256": row.get("contribution_sha256"),
                    "jats_sha256": row.get("jats_sha256"),
                }
            )
        )
    return enriched


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
        quotes = raw.get("support_quotes")
        if not isinstance(quotes, dict) or set(quotes) != set(passage_ids):
            raise ValueError("each cited passage needs an exact support quote")
        for passage_id in passage_ids:
            quote = quotes[passage_id]
            if (
                not isinstance(quote, str)
                or not quote.strip()
                or quote not in available[passage_id].excerpt
            ):
                raise ValueError("support quote is absent from cited passage")
            geography = re.findall(
                r"\b(?:[Ii]n|from|within|across)\s+([A-Z][a-z]{2,})\b",
                available[passage_id].excerpt,
            )
            if any(place.casefold() not in str(raw["text"]).casefold() for place in geography):
                raise ValueError("claim omits material passage geography")
        _validate_claim_text(str(raw["text"]), list(quotes.values()))
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


_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "these",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]+", text.casefold()) if term not in _STOPWORDS}


def _validate_claim_text(text: str, quotes: list[str]) -> None:
    """Reject obvious unsupported additions; exact quotes remain inspectable evidence."""
    claim_terms = _terms(text)
    support_terms = _terms(" ".join(quotes))
    if not claim_terms or len(claim_terms & support_terms) * 5 < len(claim_terms) * 3:
        raise ValueError("claim text is not supported by quoted passage text")
    if any(term.isdigit() and term not in support_terms for term in claim_terms):
        raise ValueError("claim introduces an unsupported number")
    causal = {"cause", "causes", "caused", "causal", "prevents", "prevented"}
    if claim_terms & causal and not claim_terms & causal <= support_terms:
        raise ValueError("claim introduces unsupported causal language")


def _validate_evidence_state(
    generated: dict[str, Any], citations: list[KnowledgeCitation]
) -> EvidenceState:
    raw = generated.get("evidence_state")
    allowed = {
        "single_study",
        "consistent",
        "limited",
        "mixed",
        "conflicting",
        "insufficient_to_compare",
    }
    if raw not in allowed:
        raise ValueError("invalid evidence state")
    cited_papers = {citation.pmid for citation in citations}
    if len(cited_papers) == 1:
        return "single_study"
    if raw == "single_study":
        raise ValueError("multi-paper answer cannot be single-study")
    if raw in {"mixed", "conflicting"} and len(cited_papers) < 2:
        raise ValueError("disagreement state requires both cited papers")
    return cast(EvidenceState, raw)
