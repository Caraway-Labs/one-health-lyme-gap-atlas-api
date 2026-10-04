"""Fixed-template Neo4j retrieval and fail-closed evidence chat."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import socket
import ssl
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Thread, get_ident
from typing import Any, Protocol, TypedDict, cast

from lyme_gap_atlas_kg import CONFIGURATION_VERSION, asset_path
from lyme_gap_atlas_shared.settings import SnowflakeSettings
from lyme_gap_atlas_shared.snowflake import connect
from neo4j import GraphDatabase, Query
from neo4j.exceptions import (
    AuthError,
    ConfigurationError,
    ConnectionAcquisitionTimeoutError,
    ServiceUnavailable,
    SessionExpired,
)
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from opentelemetry import trace

from .assistant_policy import classify_question, load_assistant_policy
from .dependency_telemetry import failure_class
from .models import (
    EvidenceState,
    KnowledgeChatRequest,
    KnowledgeChatResponse,
    KnowledgeCitation,
    KnowledgeClaim,
    SourceUsed,
)
from .neo4j_diagnostics import log_serving_graph_probe
from .telemetry_logging import emit_completion, operational_logger

logger = operational_logger(__name__)
_POST_GENERATION_RESERVE_SECONDS = 5.0
_RETRIEVAL_VERSION = "hybrid-fulltext-vector-v1"
_DIAGNOSTICS: ContextVar[dict[str, Any] | None] = ContextVar(
    "knowledge_chat_diagnostics", default=None
)
_REQUEST_ID: ContextVar[str] = ContextVar("knowledge_chat_request_id", default="unavailable")


class _RequestSnowflakeSession:
    """Lazy, exclusive worker-thread ownership; never a cross-request pool."""

    def __init__(self, settings: SnowflakeSettings, stack: ExitStack) -> None:
        self.settings = settings
        self.stack = stack
        self.owner = get_ident()
        self.connection: Any = None

    def get(self) -> Any:
        if get_ident() != self.owner:
            raise RuntimeError("Snowflake request session cannot cross worker threads")
        if self.connection is None:
            with _snowflake_timing("connection_setup"):
                self.connection = connect(self.settings)
                self.stack.callback(self.connection.close)
        elif self.connection.is_closed():
            # Never retry an uncertain reservation or write on a new connection.
            raise RuntimeError("Snowflake request session is closed")
        return self.connection


_SNOWFLAKE_SESSION: ContextVar[_RequestSnowflakeSession | None] = ContextVar(
    "knowledge_chat_snowflake_session", default=None
)


@contextmanager
def _snowflake_timing(
    operation: str, cursor: Any = None, *, query_on_failure: bool = True
) -> Iterator[None]:
    """Nested dependency details, deliberately excluded from additive stage totals."""
    started = time.perf_counter()
    outcome = "success"
    try:
        with _span(f"snowflake.{operation}", _REQUEST_ID.get()):
            yield
    except BaseException:
        outcome = "failure"
        raise
    finally:
        logger.info(
            "knowledge_chat_snowflake_operation",
            extra={
                "context": {
                    "request_id": _REQUEST_ID.get(),
                    "operation": operation,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "outcome": outcome,
                }
            },
        )


@contextmanager
def _request_snowflake_session(settings: SnowflakeSettings | None) -> Iterator[None]:
    with ExitStack() as stack:
        session = None if settings is None else _RequestSnowflakeSession(settings, stack)
        token = _SNOWFLAKE_SESSION.set(session)
        try:
            yield
        finally:
            try:
                if session is not None and session.connection is not None:
                    with _snowflake_timing("connection_close"):
                        stack.close()
            finally:
                _SNOWFLAKE_SESSION.reset(token)


@contextmanager
def _chat_snowflake_connection(settings: SnowflakeSettings) -> Iterator[Any]:
    session = _SNOWFLAKE_SESSION.get()
    if session is not None and session.settings is settings:
        connection = session.get()
        # Match the locked connector's __exit__ decision using effective session
        # parameters (login can update them), while deferring close to teardown.
        try:
            yield connection
        except BaseException:
            if not connection._session_parameters.get("AUTOCOMMIT", False):
                with _snowflake_timing("transaction_rollback"):
                    connection.rollback()
            raise
        else:
            if not connection._session_parameters.get("AUTOCOMMIT", False):
                with _snowflake_timing("transaction_commit"):
                    connection.commit()
    else:
        # Standalone adapters and injected test stores retain their original lifecycle.
        with connect(settings) as connection:
            yield connection


def _snowflake_execute(cursor: Any, sql: str, params: tuple[Any, ...], operation: str) -> None:
    # Failed execute may leave sfqid from the previous CALL on a reused cursor.
    # Omit attribution on failure rather than mutate SDK cursor state.
    with _snowflake_timing(f"{operation}.execute", cursor, query_on_failure=False):
        cursor.execute(sql, params)


def _snowflake_fetchone(cursor: Any, operation: str) -> Any:
    with _snowflake_timing(f"{operation}.fetch", cursor):
        return cursor.fetchone()


def _safe_identifier(value: object) -> str | None:
    """Only emit bounded operational identifiers, never arbitrary provider text."""
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value):
        return value
    return None


_ASK_OUTCOMES = frozenset({
    "answered", "safety_refusal", "no_evidence", "capacity_limited", "embedding_failure",
    "neo4j_timeout", "neo4j_query_failure", "retrieval_dependency_unavailable",
    "retrieval_failure", "budget_failure", "deadline_exhausted", "generation_timeout",
    "provider_rejection", "generation_transport_error", "malformed_generated_json",
    "generation_error", "corrective_retry_exhausted", "grounding_validation_failed",
    "provenance_failure", "persistence_failure", "invalid_conversation_capability",
    "authorization_dependency_failure", "unhandled_error",
})
_PRODUCT_OUTCOMES = frozenset({"safety_refusal", "no_evidence", "capacity_limited"})


def _completion_attributes(context: dict[str, Any]) -> None:
    """Closed vocabulary and capped counts only; arbitrary diagnostics are never copied."""
    outcome = context.get("outcome")
    outcome = outcome if outcome in _ASK_OUTCOMES else "unhandled_error"
    attributes: dict[str, str | int] = {
        "outcome": outcome,
        "outcome_class": (
            "answered" if outcome == "answered" else
            "intentional_abstention" if outcome in _PRODUCT_OUTCOMES else "failure"
        ),
        "provider": "openai",
        "configuration_version": CONFIGURATION_VERSION,
        "retrieval_version": _RETRIEVAL_VERSION,
    }
    validation = context.get("validation_outcome")
    allowed_validation = _GROUNDING_REASONS | {"passed", "not_run", "invalid_generated_shape"}
    attributes["validation_outcome"] = (
        validation if isinstance(validation, str) and validation in allowed_validation
        else "invalid_generated_shape"
    )
    evidence = context.get("evidence_state")
    evidence_states = {
        "single_study", "consistent", "limited", "mixed", "conflicting",
        "insufficient_to_compare", "no_relevant_corpus_evidence", "evidence_unavailable",
        "not_applicable",
    }
    attributes["evidence_state"] = evidence if evidence in evidence_states else "unavailable"
    # Model is intentionally omitted: a format check alone is not a closed vocabulary.
    for key, maximum in (("generation_attempts", 2), ("retrieval_passage_count", 100),
                         ("retrieval_paper_count", 100)):
        value = context.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            attributes[key] = max(0, min(value, maximum))
    with suppress(Exception):
        span = trace.get_current_span()
        for key, value in attributes.items():
            span.set_attribute(f"atlas.ask_atlas.{key}", value)


@contextmanager
def _span(name: str, request_id: str, **attributes: str | int | bool | None) -> Iterator[None]:
    """Tracing is optional and exporter failures must not affect answers."""
    try:
        span = trace.get_tracer(__name__).start_span(f"knowledge_chat.{name}")
    except Exception:
        span = None
    try:
        if span is not None:
            try:
                span.set_attribute("request.id", request_id)
                for key, value in attributes.items():
                    if value is not None:
                        span.set_attribute(key, value)
            except Exception:
                pass
        try:
            with trace.use_span(span, end_on_exit=False, record_exception=False,
                                set_status_on_exception=False) if span is not None else ExitStack():
                yield
        except Exception as exc:
            if span is not None:
                with suppress(Exception):
                    span.set_attribute("stage.outcome", "failure")
                    span.set_attribute("failure.type", failure_class(exc))
            raise
        else:
            if span is not None:
                with suppress(Exception):
                    span.set_attribute("stage.outcome", "success")
    finally:
        if span is not None:
            with suppress(Exception):
                span.end()


@contextmanager
def _timed_stage(request_id: str, stage: str) -> Iterator[None]:
    """Record timings without recording user text, evidence, tokens, or model output."""
    started = time.perf_counter()
    error_type: str | None = None
    with _span(stage, request_id):
        try:
            yield
        except Exception as exc:
            error_type = failure_class(exc)
            raise
        finally:
            diagnostics = _DIAGNOSTICS.get()
            if diagnostics is not None:
                diagnostics.setdefault("stage_latencies_ms", {})[stage] = round(
                    (time.perf_counter() - started) * 1000
                )
            logger.info(
                "knowledge_chat_stage",
                extra={
                    "context": {
                        "request_id": request_id,
                        "stage": stage,
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "outcome": "failure" if error_type else "success",
                        "error_type": error_type,
                    }
                },
            )


_GROUNDING_REASONS = {
    "unsupported passage citation",
    "invented or missing PMID",
    "each cited passage needs an exact support quote",
    "support quote is absent from cited passage",
    "claim text is not supported by quoted passage text",
    "claim introduces an unsupported number",
    "claim introduces unsupported causal language",
    "answer has no grounded claims",
    "invalid evidence state",
    "multi-paper answer cannot be single-study",
    "disagreement state requires both cited papers",
}


def _grounding_reason(exc: Exception) -> str:
    reason = str(exc)
    return reason if reason in _GROUNDING_REASONS else "invalid_generated_shape"


def _shape_diagnostics(candidate: object, evidence: list[Evidence]) -> dict[str, Any]:
    """Bounded structural counts; IDs and candidate strings never leave this function."""
    expected = {"answer", "evidence_state", "claims"}
    if not isinstance(candidate, dict):
        return {"top_level_type": _shape_type(candidate)}
    claims = candidate.get("claims")
    available = {item.passage_id: item.pmid for item in evidence}
    result: dict[str, Any] = {
        "top_level_type": "object",
        "expected_fields_present": len(expected & candidate.keys()),
        "expected_fields_missing": len(expected - candidate.keys()),
        "claims_type": _shape_type(claims),
        "claim_count": min(len(claims), 1000) if isinstance(claims, list) else 0,
    }
    shapes: list[dict[str, Any]] = []
    if isinstance(claims, list):
        for raw in claims[:5]:
            if not isinstance(raw, dict):
                shapes.append({"type": _shape_type(raw)})
                continue
            ids = raw.get("passage_ids")
            pmids = raw.get("pmids")
            quotes = raw.get("support_quotes")
            ids_list = ids if isinstance(ids, list) else []
            pmids_list = pmids if isinstance(pmids, list) else []
            quote_ids: list[object] = []
            if isinstance(quotes, dict):
                quote_ids = list(quotes)
            elif isinstance(quotes, list):
                quote_ids = [item.get("passage_id") for item in quotes if isinstance(item, dict)]
            cited = {item for item in ids_list if isinstance(item, str)}
            quoted = {item for item in quote_ids if isinstance(item, str)}
            shapes.append(
                {
                    "passage_id_count": min(len(ids_list), 1000),
                    "pmid_count": min(len(pmids_list), 1000),
                    "support_quote_count": min(len(quote_ids), 1000),
                    "quote_representation": "mapping"
                    if isinstance(quotes, dict)
                    else ("record_list" if isinstance(quotes, list) else "other"),
                    "duplicate_passage_id_count": min(len(ids_list) - len(cited), 1000),
                    "duplicate_quote_id_count": min(len(quote_ids) - len(quoted), 1000),
                    "missing_quote_id_count": min(len(cited - quoted), 1000),
                    "extra_quote_id_count": min(len(quoted - cited), 1000),
                    "unknown_passage_id_count": min(len(cited - available.keys()), 1000),
                    "pmid_mismatch": (
                        set(pmids_list) != {available[item] for item in cited if item in available}
                        if all(isinstance(item, str) for item in pmids_list)
                        else True
                    ),
                }
            )
    result["claim_shapes"] = shapes
    return result


def _shape_type(value: object) -> str:
    kind = type(value)
    return kind.__name__ if kind in {dict, list, str, int, float, bool, type(None)} else "other"


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
    source_used: SourceUsed


class Retriever(Protocol):
    def ready(self) -> bool: ...

    def search(self, message: str, request_id: str) -> list[Evidence]: ...


class Answerer(Protocol):
    def answer(
        self,
        message: str,
        evidence: list[Evidence],
        safety_id: str,
        *,
        timeout_seconds: float,
        correction: bool = False,
    ) -> dict[str, Any]: ...


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


class EmbeddingFailure(RuntimeError):
    """Embedding provider failed before retrieval."""


class Neo4jQueryFailure(RuntimeError):
    """Fixed retrieval query failed after embedding."""


class AuthorizationDependencyFailure(RuntimeError):
    """Conversation capability store failed before chat processing."""


def _neo4j_failure_category(error: BaseException) -> str:
    """Classify bounded exception chains without copying messages or addresses."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending and len(chain) < 8:
        current = pending.pop()
        if id(current) in seen:
            continue
        chain.append(current)
        seen.add(id(current))
        # Driver `raise ... from None` suppresses text display, not type evidence.
        # Inspect types only; never format any exception or traceback.
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions[:8]))
    for exception_type, category in (
        (AuthError, "authentication"),
        (ConfigurationError, "configuration"),
        (ssl.SSLError, "tls"),
        (socket.gaierror, "dns"),
        (TimeoutError, "timeout"),
        (ConnectionAcquisitionTimeoutError, "timeout"),
        (ConnectionRefusedError, "connection_refused"),
        (ServiceUnavailable, "service_unavailable"),
        (SessionExpired, "session_expired"),
    ):
        if any(isinstance(item, exception_type) for item in chain):
            return category
    return "unknown"


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
        # Diagnostics must not gate App Platform startup or health readiness.
        try:
            Thread(
                target=log_serving_graph_probe,
                args=(self._driver, _neo4j_failure_category),
                name="neo4j-serving-probe",
                daemon=True,
            ).start()
        except Exception as error:
            logger.warning(
                "knowledge_chat_serving_graph_probe",
                extra={"context": {
                    "probe": "startup", "database": "neo4j", "outcome": "failure",
                    "error_category": _neo4j_failure_category(error),
                }},
                exc_info=False,
            )

    def ready(self) -> bool:
        try:
            self._driver.verify_connectivity()
        except Exception as error:
            logger.warning(
                "neo4j_connectivity_unavailable",
                extra={
                    "context": {
                        "request_id": _REQUEST_ID.get(),
                        "error_category": _neo4j_failure_category(error),
                    }
                },
                exc_info=False,
            )
            return False
        return True

    def search(self, message: str, request_id: str) -> list[Evidence]:
        try:
            with _timed_stage(request_id, "embedding"):
                embedding = (
                    self._openai.with_options(max_retries=0, timeout=5)
                    .embeddings.create(
                        model="text-embedding-3-small", input=message, dimensions=1024
                    )
                    .data[0]
                    .embedding
                )
        except Exception as exc:
            raise EmbeddingFailure from exc
        started = time.perf_counter()
        category = "success"
        records: list[Any] = []
        try:
            with _timed_stage(request_id, "neo4j_retrieval"):
                records, _, _ = self._driver.execute_query(
                    Query(HYBRID_SEARCH, timeout=5),
                    {"query": message, "embedding": embedding},
                    database_="neo4j",
                )
        except Exception as exc:
            category = "neo4j_timeout" if isinstance(exc, TimeoutError) else "neo4j_query_failure"
            raise Neo4jQueryFailure from exc
        finally:
            logger.info(
                "knowledge_chat_retrieval",
                extra={
                    "context": {
                        "request_id": request_id,
                        "retrieval_version": _RETRIEVAL_VERSION,
                        "configuration_version": CONFIGURATION_VERSION,
                        "requested_top_k": 20,
                        "candidate_passage_count": len(records),
                        "unique_paper_count": len({str(row["pmid"]) for row in records}),
                        "empty": not bool(records),
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "outcome": category,
                    }
                },
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
        with (
            _chat_snowflake_connection(self._settings) as connection,
            connection.cursor() as cursor,
        ):
            _snowflake_execute(
                cursor,
                f"CALL {db}.GOVERNANCE.SP_VERIFY_KG_CONVERSATION_TOKEN(%s,%s)",
                (conversation_id, token_hash),
                "authorization",
            )
            row = _snowflake_fetchone(cursor, "authorization")
            return bool(row and row[0])

    def reserve(self, request_id: str) -> bool:
        db = _governance_database(self._settings)
        with (
            _chat_snowflake_connection(self._settings) as connection,
            connection.cursor() as cursor,
        ):
            _snowflake_execute(
                cursor,
                f"CALL {db}.GOVERNANCE.SP_RESERVE_KG_LLM_BUDGET(%s,%s,%s,%s,%s,%s,%s)",
                ("chat", request_id, "openai", "gpt-5.6-luna", 0.05, 5, 100),
                "budget",
            )
            row = _snowflake_fetchone(cursor, "budget")
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
        with (
            _chat_snowflake_connection(self._settings) as connection,
            connection.cursor() as cursor,
        ):
            for turn_request_id, role, body, status, turn_citations in turns:
                _snowflake_execute(
                    cursor,
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
                    "persistence",
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
        with (
            _chat_snowflake_connection(self._settings) as connection,
            connection.cursor() as cursor,
        ):
            _snowflake_execute(
                cursor,
                f"CALL {db}.GOVERNANCE.SP_LOOKUP_RETRIEVAL_CORPUS_PROVENANCE(PARSE_JSON(%s))",
                (json.dumps(unique),),
                "provenance",
            )
            row = _snowflake_fetchone(cursor, "provenance")
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


_CLAIM_GROUNDING_INSTRUCTIONS = (
    "Build every claim from its support quotes, not the other way around. "
    "For each claim, first select returned passage IDs and their matching PMIDs, then select "
    "an exact verbatim excerpt substring as the support quote for every cited passage. "
    "Set support_quotes to an object keyed by each cited passage ID, for example "
    '{"passage-1": "verbatim words from that passage excerpt"}; use the exact IDs in '
    "passage_ids as keys, with no other keys. "
    "Every claim, including a claim about a study limitation, must have at least one returned "
    "passage ID, its matching PMID, and an exact support quote from that passage. Never add "
    "a claim with empty passage_ids, pmids, or support_quotes; omit any finding or limitation "
    "that the supplied excerpts do not explicitly support. Do not add an uncited concluding "
    "claim or a placeholder claim to answer every part of the question. "
    "Only then write one short, atomic claim about a finding explicitly present in those quotes. "
    "Use a close extractive paraphrase: preserve the source's important scientific nouns, "
    "entities, verbs, relationships, and material species, geography, population, and outcome "
    "qualifiers. Avoid distant synonyms, smoother but unsupported interpretation, and combining "
    "unrelated findings. Make each claim no broader than its cited quotes. Do not add unsupported "
    "numbers or causal language. The validated claims[].text are the user-visible answer units; "
    "keep answer consistent with those claims and do not add separate broader findings. "
)


class OpenAIAnswerer:
    def __init__(self, client: OpenAI, model: str = "gpt-5.6-luna") -> None:
        self._client = client
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    def answer(
        self,
        message: str,
        evidence: list[Evidence],
        safety_id: str,
        *,
        timeout_seconds: float,
        correction: bool = False,
    ) -> dict[str, Any]:
        policy = load_assistant_policy()
        question_class = classify_question(message)
        strictness = policy.strictness_for(question_class)
        # The excerpt is the literal grounding source. URLs are resolved from
        # validated Evidence after generation; summaries cannot support quotes.
        passages = [
            {
                "passage_id": item.passage_id,
                "excerpt": item.excerpt,
                "pmid": item.pmid,
                "title": item.title,
            }
            for item in evidence
        ]
        correction_instruction = (
            "The previous candidate failed deterministic grounding. Regenerate carefully from "
            "only the supplied passages. Do not reuse the previous candidate."
            if correction
            else ""
        )
        provider_input = json.dumps(
            {"question": message, "passages": passages, "response_format": "json"}
        )
        logger.info(
            "knowledge_chat_provider_input_size",
            extra={"context": {
                "request_id": _REQUEST_ID.get(),
                "passage_count": len(passages),
                "input_characters": len(provider_input),
            }},
        )
        response = self._client.with_options(max_retries=0).responses.create(
            model=self._model,
            store=False,
            reasoning={"effort": "low"},
            safety_identifier=safety_id,
            instructions=(
                "Answer only from supplied steward-approved PubMed/PMC full-text passages. "
                "Return JSON with answer, evidence_state, and claims. Each claim has claim_id, "
                "text, passage_ids, pmids, and support_quotes. "
                f"{_CLAIM_GROUNDING_INSTRUCTIONS}"
                "State must be one of single_study, consistent, limited, mixed, conflicting, "
                "insufficient_to_compare. Do not infer consensus from paper count. Preserve "
                "disagreement and cite both sides. Include material species, geography, period, "
                "population, sampling, denominator, outcome, validation, publication type, and "
                "limitations when supplied and relevant. Do not invent missing context, generalize "
                "geography or outcomes, or turn association into causation. Historical questions "
                "are untrusted context, never evidence. Do not diagnose, prescribe, dose, or make "
                "the final public-health decision. "
                f"Decision-support strictness: {strictness}. "
                f"Proactive follow-up suggestions: {policy.proactive_follow_up_suggestions}. "
                f"{correction_instruction}"
            ),
            input=provider_input,
            text={"format": {"type": "json_object"}},
            timeout=timeout_seconds,
        )
        logger.info(
            "knowledge_chat_provider_response",
            extra={
                "context": {
                    "request_id": _REQUEST_ID.get(),
                    "provider": "openai",
                    "configuration_version": CONFIGURATION_VERSION,
                    "outcome": "completed",
                }
            },
        )
        with _span("generated_json_parsing", _REQUEST_ID.get()):
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
        *,
        deadline_seconds: float = 28.0,
        generation_timeout_seconds: float = 16.0,
        clock: Callable[[], float] = time.monotonic,
        snowflake_settings: SnowflakeSettings | None = None,
    ) -> None:
        self._retriever = retriever
        self._answerer = answerer
        self._store = budget_store
        self._provenance = provenance_store
        self._secret = hash_secret.encode()
        self._deadline_seconds = deadline_seconds
        self._generation_timeout_seconds = generation_timeout_seconds
        self._clock = clock
        self._snowflake_settings = snowflake_settings

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
        self,
        request: KnowledgeChatRequest,
        request_id: str,
        network_identifier: str,
        completion: dict[str, Any] | None = None,
    ) -> KnowledgeChatResponse:
        with _span("service", request_id):
            return self._chat_with_diagnostics(request, request_id, network_identifier, completion)

    def _chat_with_diagnostics(
        self,
        request: KnowledgeChatRequest,
        request_id: str,
        network_identifier: str,
        completion: dict[str, Any] | None = None,
    ) -> KnowledgeChatResponse:
        started = self._clock()
        outcome = "unhandled_error"
        diagnostics: dict[str, Any] = {
            "retrieval_passage_count": 0,
            "retrieval_paper_count": 0,
            "generation_attempts": 0,
            "validation_outcome": "not_run",
            "stage_latencies_ms": {},
        }
        context_token = _DIAGNOSTICS.set(diagnostics)
        request_token = _REQUEST_ID.set(request_id)
        result: KnowledgeChatResponse | None = None
        try:
            with _request_snowflake_session(self._snowflake_settings):
                result, outcome = self._chat_impl(
                    request, request_id, network_identifier, started + self._deadline_seconds
                )
            return result
        except Exception as exc:
            if result is None and isinstance(exc, ValueError):
                outcome = "invalid_conversation_capability"
            elif result is None and isinstance(exc, AuthorizationDependencyFailure):
                outcome = "authorization_dependency_failure"
            else:
                outcome = "unhandled_error"
            result = None
            with suppress(Exception):
                trace.get_current_span().set_status(trace.StatusCode.ERROR)
            raise
        finally:
            _DIAGNOSTICS.reset(context_token)
            _REQUEST_ID.reset(request_token)
            context = {
                "request_id": request_id,
                "service_duration_ms": round((self._clock() - started) * 1000),
                "outcome": outcome,
                "evidence_state": result.evidence_state if result is not None else None,
                "provider": "openai",
                "model_id": _safe_identifier(getattr(self._answerer, "model_id", None)),
                "configuration_version": CONFIGURATION_VERSION,
                "retrieval_version": _RETRIEVAL_VERSION,
              **diagnostics,
          }
            _completion_attributes(context)
            if completion is None:
                emit_completion(logger, "knowledge_chat_total", context)
            else:
                completion.update(context)

    def _chat_impl(
        self,
        request: KnowledgeChatRequest,
        request_id: str,
        network_identifier: str,
        deadline: float,
    ) -> tuple[KnowledgeChatResponse, str]:
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
            "source_used": "literature_evidence",
        }
        if bool(request.conversation_id) != bool(request.conversation_token):
            raise ValueError("conversation_id and conversation_token must be provided together")
        if request.conversation_id and self._store is not None:
            try:
                authorized = self._store.authorize(conversation_id, self._hash(token))
            except Exception as exc:
                raise AuthorizationDependencyFailure from exc
            if not authorized:
                raise ValueError("conversation capability is invalid")
        safety_id = self._hash(network_identifier)

        def unavailable(outcome: str) -> tuple[KnowledgeChatResponse, str]:
            return (
                KnowledgeChatResponse(
                    **base,
                    status="evidence_unavailable",
                    answer=EVIDENCE_UNAVAILABLE,
                    evidence_state="evidence_unavailable",
                ),
                outcome,
            )

        def persist(result: KnowledgeChatResponse) -> None:
            with _timed_stage(request_id, "conversation_persistence"):
                self._persist(request, result, token, safety_id)

        with _timed_stage(request_id, "safety_classification"):
            unsafe = _unsafe_request(request.message)
        if unsafe:
            result = KnowledgeChatResponse(
                **base,
                status="safety_refusal",
                answer=SAFETY_REFUSAL,
                evidence_state="not_applicable",
            )
            try:
                persist(result)
                return result, "safety_refusal"
            except Exception:
                return unavailable("persistence_failure")
        try:
            # Browser-local history supplies only prior user questions as retrieval
            # context. Prior assistant text is never admitted as evidence.
            prior_questions = [turn.content for turn in request.history if turn.role == "user"][-2:]
            contextual_question = "\n".join([*prior_questions, request.message])
            evidence = self._retriever.search(contextual_question, request_id)
            diagnostics = _DIAGNOSTICS.get()
            if diagnostics is not None:
                diagnostics["retrieval_passage_count"] = len(evidence)
                diagnostics["retrieval_paper_count"] = len({item.pmid for item in evidence})
        except EmbeddingFailure:
            return unavailable("embedding_failure")
        except Neo4jQueryFailure as exc:
            return unavailable("neo4j_timeout" if isinstance(exc.__cause__, TimeoutError)
                               else "neo4j_query_failure")
        except RuntimeError:
            return unavailable("retrieval_dependency_unavailable")
        except Exception:
            return unavailable("retrieval_failure")
        if not evidence:
            result = KnowledgeChatResponse(
                **base,
                status="no_evidence",
                answer=NO_EVIDENCE,
                evidence_state="no_relevant_corpus_evidence",
            )
            try:
                persist(result)
                return result, "no_evidence"
            except Exception:
                return unavailable("persistence_failure")
        if self._clock() >= deadline:
            return unavailable("deadline_exhausted")
        try:
            if self._store is not None:
                with _timed_stage(request_id, "budget_reservation"):
                    allowed = self._store.reserve(request_id)
                if not allowed:
                    return (
                        KnowledgeChatResponse(
                            **base,
                            status="capacity_limited",
                            answer=CAPACITY_LIMITED,
                            evidence_state="not_applicable",
                        ),
                        "capacity_limited",
                    )
        except Exception:
            return unavailable("budget_failure")
        if self._clock() >= deadline:
            return unavailable("deadline_exhausted")

        for attempt in (1, 2):
            diagnostics = _DIAGNOSTICS.get()
            remaining = deadline - self._clock()
            # Reserve time for grounding, provenance, Snowflake persistence,
            # and serialization after the provider call. The production QA
            # request spent ~7 seconds before generation and timed out at 10.
            if remaining < _POST_GENERATION_RESERVE_SECONDS + 3:
                return unavailable("deadline_exhausted")
            timeout = min(
                self._generation_timeout_seconds,
                remaining - _POST_GENERATION_RESERVE_SECONDS,
            )
            try:
                with _timed_stage(request_id, f"answer_generation_attempt_{attempt}"):
                    if diagnostics is not None:
                        diagnostics["generation_attempts"] = attempt
                    generated = self._answerer.answer(
                        contextual_question,
                        evidence,
                        safety_id,
                        timeout_seconds=timeout,
                        correction=attempt == 2,
                    )
            except (APITimeoutError, TimeoutError):
                return unavailable("generation_timeout")
            except APIStatusError as exc:
                logger.warning("knowledge_chat_provider_failure", extra={"context": {
                    "request_id": request_id,
                    "provider": "openai",
                    "failure_category": "provider_rejection",
                    "status_code": exc.status_code,
                }})
                return unavailable("provider_rejection")
            except (APIConnectionError, OSError):
                return unavailable("generation_transport_error")
            except json.JSONDecodeError:
                return unavailable("malformed_generated_json")
            except Exception:
                return unavailable("generation_error")
            try:
                with _timed_stage(request_id, f"grounding_validation_attempt_{attempt}"):
                    claims, citations = _validate_grounding(generated, evidence)
                    evidence_state = _validate_evidence_state(generated, citations)
            except Exception as exc:
                if diagnostics is not None:
                    diagnostics["validation_outcome"] = _grounding_reason(exc)
                logger.info(
                    "knowledge_chat_grounding_rejected",
                    extra={
                        "context": {
                            "request_id": request_id,
                            "attempt": attempt,
                            "reason": _grounding_reason(exc),
                            "error_type": "validation_error",
                            "shape": _shape_diagnostics(generated, evidence),
                            "corrective_retry_attempted": attempt == 2,
                        }
                    },
                )
                if attempt == 2:
                    return unavailable("corrective_retry_exhausted")
                continue
            if diagnostics is not None:
                diagnostics["validation_outcome"] = "passed"
            try:
                with _timed_stage(request_id, "provenance_enrichment"):
                    citations = _enrich_citations(citations, self._provenance, request_id)
                result = KnowledgeChatResponse(
                    **base,
                    status="answered",
                    evidence_state=evidence_state,
                    answer="\n\n".join(claim.text for claim in claims),
                    model_id=getattr(self._answerer, "model_id", None),
                    claims=claims,
                    citations=citations,
                )
            except Exception:
                return unavailable("provenance_failure")
            try:
                persist(result)
            except Exception:
                return unavailable("persistence_failure")
            return result, "answered"
        return unavailable("grounding_validation_failed")


def _enrich_citations(
    citations: list[KnowledgeCitation],
    store: CorpusProvenanceStore | None,
    request_id: str,
) -> list[KnowledgeCitation]:
    """Attach optional corpus provenance; never fail a Neo4j-grounded answer."""
    if store is None or not citations:
        return citations
    try:
        by_pmid = store.lookup([citation.pmid for citation in citations])
    except Exception as exc:
        logger.warning(
            "retrieval_corpus.provenance_lookup_failed",
            extra={
                "context": {
                    "request_id": request_id,
                    "stage": "provenance_enrichment",
                    "outcome": "provenance_lookup_failure",
                    "error_type": failure_class(exc),
                }
            },
        )
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
        quotes = _support_quotes(raw.get("support_quotes"), passage_ids)
        for passage_id in passage_ids:
            quote = quotes[passage_id]
            if (
                not isinstance(quote, str)
                or not quote.strip()
                or quote not in available[passage_id].excerpt
            ):
                raise ValueError("support quote is absent from cited passage")
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


def _support_quotes(value: Any, passage_ids: list[str]) -> dict[str, Any]:
    """Accept two unambiguous model JSON shapes, then apply the same exact checks."""
    if isinstance(value, dict):
        quotes = value
    elif isinstance(value, list):
        # Models sometimes serialize a keyed mapping as records. Never infer a
        # passage ID from position or fill in a missing quote from retrieval.
        quotes = {}
        for item in value:
            if not isinstance(item, dict) or set(item) != {"passage_id", "quote"}:
                raise ValueError("each cited passage needs an exact support quote")
            passage_id = item["passage_id"]
            if not isinstance(passage_id, str) or passage_id in quotes:
                raise ValueError("each cited passage needs an exact support quote")
            quotes[passage_id] = item["quote"]
    else:
        raise ValueError("each cited passage needs an exact support quote")
    if set(quotes) != set(passage_ids):
        raise ValueError("each cited passage needs an exact support quote")
    return quotes


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
