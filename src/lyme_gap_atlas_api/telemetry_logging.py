"""Closed completion-event schema; shared observability remains the log backend."""

import logging
import math
import re
from contextlib import suppress
from typing import Any

from opentelemetry import trace

from .telemetry import FAILURES, METHODS, OUTCOMES, REQUEST_CORRELATION, ROUTES


class OperationalLogger(logging.LoggerAdapter):  # type: ignore[type-arg]
    """Keep handler/formatter failures from breaking any application operation."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        return msg, kwargs

    def log(self, level: int, msg: object, *args: Any, **kwargs: Any) -> None:
        with suppress(Exception):
            kwargs["exc_info"] = False
            kwargs["stack_info"] = False
            super().log(level, msg, *args, **kwargs)


def operational_logger(name: str) -> OperationalLogger:
    return OperationalLogger(logging.getLogger(name))


def operational_request_id() -> str:
    """HTTP correlation only; a privacy/workflow resource UUID is never a request ID."""
    correlation = REQUEST_CORRELATION.get()
    if correlation != "unavailable":
        return correlation
    with suppress(Exception):
        value = getattr(trace.get_current_span(), "attributes", {}).get("request.id")
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value):
            return value
    return "unavailable"


class _DependencyLogFilter(logging.Filter):
    """Third-party SDK logs bypass our event schemas and can contain full URLs."""

    def filter(self, record: logging.LogRecord) -> bool:
        prefixes = (
            "httpx",
            "httpcore",
            "openai",
            "snowflake",
            "neo4j",
            "urllib3",
            "opentelemetry.sdk",
            "opentelemetry.exporter",
            "uvicorn",
        )
        if not record.name.startswith(prefixes):
            return True
        if record.levelno < logging.WARNING:
            return False
        record.msg = (
            ("api_server_error" if record.levelno >= logging.ERROR else "api_server_warning")
            if record.name.startswith("uvicorn")
            else (
                "telemetry_backend_warning"
                if record.name.startswith("opentelemetry")
                else "dependency_sdk_warning"
            )
        )
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        record.context = {
            "failure_class": "unhandled_error"
            if record.name.startswith("uvicorn")
            else "dependency_error"
        }
        return True


def protect_dependency_logs() -> None:
    # Uvicorn's default parent owns stderr with propagate=False; filtering root
    # alone cannot sanitize its exception chain. Filter direct emitters too so a
    # later handler replacement on those loggers cannot bypass the boundary.
    loggers = [logging.getLogger(name) for name in ("uvicorn", "uvicorn.error", "uvicorn.access")]
    for logger in loggers:
        if not any(isinstance(item, _DependencyLogFilter) for item in logger.filters):
            logger.addFilter(_DependencyLogFilter())
    handlers = [
        handler for logger in [logging.getLogger(), *loggers] for handler in logger.handlers
    ]
    for handler in handlers:
        if not any(isinstance(item, _DependencyLogFilter) for item in handler.filters):
            handler.addFilter(_DependencyLogFilter())


# Mirrors the service vocabulary reviewed in PR158; HTTP-only terminal causes are additive.
ASK_OUTCOMES = frozenset(
    {
        "answered",
        "safety_refusal",
        "no_evidence",
        "capacity_limited",
        "embedding_failure",
        "neo4j_timeout",
        "neo4j_query_failure",
        "retrieval_dependency_unavailable",
        "retrieval_failure",
        "budget_failure",
        "budget_exhausted",
        "deadline_exhausted",
        "generation_timeout",
        "provider_rejection",
        "generation_transport_error",
        "malformed_generated_json",
        "generation_error",
        "corrective_retry_exhausted",
        "grounding_validation_failed",
        "provenance_failure",
        "persistence_failure",
        "invalid_conversation_capability",
        "authorization_dependency_failure",
        "unhandled_error",
        "response_serialization_failure",
        "request_validation_failure",
        "rate_limited",
        "route_unavailable",
        "source_unavailable",
        "insufficient_evidence",
        "needs_clarification",
        "unsupported_request",
        "query_too_broad",
    }
)
OPERATIONAL_OUTCOMES = frozenset({
    "answered", "abstained", "validation_failure", "dependency_failure",
    "provider_failure", "budget_exhaustion", "internal_failure",
})


def assistant_operational_outcome(outcome: str, cause: str | None = None) -> str:
    """Classify only closed, non-content service and routing results."""
    if cause in {"capacity_limited", "budget_failure", "budget_exhausted",
                 "deadline_exhausted", "rate_limited"}:
        return "budget_exhaustion"
    if cause in {"provider_rejection", "generation_transport_error",
                 "generation_timeout", "generation_error", "embedding_failure"}:
        return "provider_failure"
    if cause in {"corrective_retry_exhausted", "grounding_validation_failed",
                 "malformed_generated_json"}:
        return "validation_failure"
    if cause in {"neo4j_timeout", "neo4j_query_failure", "retrieval_dependency_unavailable",
                 "retrieval_failure", "provenance_failure", "persistence_failure",
                 "authorization_dependency_failure", "route_unavailable"}:
        return "dependency_failure"
    if outcome == "answered":
        return "answered"
    if outcome in {"insufficient_evidence", "no_evidence", "needs_clarification",
                   "safety_refusal", "unsupported_request", "query_too_broad"}:
        return "abstained"
    if outcome == "request_validation_failure":
        return "validation_failure"
    if outcome == "rate_limited":
        return "budget_exhaustion"
    if outcome == "source_unavailable":
        return "dependency_failure"
    return "internal_failure"
STAGES = frozenset(
    {
        "safety_classification",
        "embedding",
        "neo4j_readiness",
        "neo4j_retrieval",
        "budget_reservation",
        "authorization",
        "provenance_enrichment",
        "conversation_persistence",
        "generated_json_parsing",
        "answer_generation_attempt_1",
        "answer_generation_attempt_2",
        "grounding_validation_attempt_1",
        "grounding_validation_attempt_2",
    }
)
VALIDATIONS = frozenset(
    {
        "passed",
        "not_run",
        "invalid_generated_shape",
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
)
EVIDENCE_STATES = frozenset(
    {
        "single_study",
        "consistent",
        "limited",
        "mixed",
        "conflicting",
        "insufficient_to_compare",
        "no_relevant_corpus_evidence",
        "evidence_unavailable",
        "not_applicable",
    }
)


def _number(value: Any, maximum: int) -> int | None:
    if type(value) is int:
        return max(0, min(value, maximum))
    if isinstance(value, float) and math.isfinite(value):
        return max(0, min(round(value), maximum))
    return None


def completion_context(event: str, context: dict[str, Any]) -> dict[str, Any]:
    """Never copy arbitrary diagnostics, even when injected by a future route/store."""
    result: dict[str, Any] = {"telemetry_schema_version": "1"}
    identity = context.get("request_id")
    result["request_id"] = (
        identity
        if isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", identity)
        else "unavailable"
    )
    is_chat = event == "knowledge_chat_total"
    enums = {
        "method": METHODS | {"OTHER"},
        "status_class": {f"{i}xx" for i in range(1, 6)} | {"unknown"},
        "outcome": ASK_OUTCOMES if is_chat else OUTCOMES,
        "failure_class": FAILURES,
        "evidence_state": EVIDENCE_STATES,
        "validation_outcome": VALIDATIONS,
        "provider": {"openai"},
        "configuration_version": {"kg-v1.0.0"},
        "retrieval_version": {"hybrid-fulltext-vector-v1"},
        "operational_outcome": OPERATIONAL_OUTCOMES,
        "service_outcome": ASK_OUTCOMES,
    }
    for key, allowed in enums.items():
        value = context.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    if "outcome" not in result:
        result["outcome"] = "unhandled_error" if is_chat else "server_error"
    # Path must originate from route normalization, never URL/query extraction.
    path = context.get("path")
    if path is not None:
        result["path"] = path if isinstance(path, str) and path in ROUTES else "unmatched"
    for key in ("status_code", "http_status"):
        value = context.get(key)
        if type(value) is int and 100 <= value <= 599:
            result[key] = value
    for key, maximum in (
        ("duration_ms", 86_400_000),
        ("service_duration_ms", 86_400_000),
        ("generation_attempts", 2),
        ("retrieval_passage_count", 100),
        ("retrieval_paper_count", 100),
    ):
        value = _number(context.get(key), maximum)
        if value is not None:
            result[key] = value
    stages = context.get("stage_latencies_ms")
    if isinstance(stages, dict):
        result["stage_latencies_ms"] = {
            stage: duration
            for stage in STAGES
            if (duration := _number(stages.get(stage), 86_400_000)) is not None
        }
    return result


def emit_completion(
    logger: logging.Logger | OperationalLogger, event: str, context: dict[str, Any]
) -> None:
    """Log construction/emission failures must never change request behavior."""
    if event not in {"api_request_completed", "api_request_failed", "knowledge_chat_total"}:
        return
    with suppress(Exception):
        logger.log(
            logging.ERROR if event == "api_request_failed" else logging.INFO,
            event,
            extra={"context": completion_context(event, context)},
        )
