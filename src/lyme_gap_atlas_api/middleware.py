import asyncio
import hashlib
import json
import math
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from .telemetry import enrich_request, request_correlation, request_dimensions
from .telemetry import request_id as normalize_request_id
from .telemetry_logging import assistant_operational_outcome, emit_completion, operational_logger

logger = operational_logger(__name__)

_PUBLIC_COLLECTIONS = {
    "/v1/indicators", "/v1/measures", "/v1/sources", "/v1/observations",
    "/v1/intelligence/items", "/v1/intelligence/sources",
}
_PUBLIC_DETAILS = ("/v1/indicators/", "/v1/measures/", "/v1/sources/", "/v1/methodologies/")


def _log_route(request: Request) -> str:
    """Keep request identity and query values out of operational telemetry."""
    path = request.url.path
    if path in _PUBLIC_COLLECTIONS:
        return path
    for prefix in _PUBLIC_DETAILS:
        if path.startswith(prefix) and path[len(prefix):] and "/" not in path[len(prefix):]:
            return prefix + "{id}"
    route = request.scope.get("route")
    return str(route.path) if route is not None else "unmatched"


class PublicReadProtectionMiddleware(BaseHTTPMiddleware):
    """Single-process, ephemeral anonymous protection for canonical public reads."""

    def __init__(
        self, app: object, requests_per_minute: int, concurrent_requests: int, max_query_bytes: int
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limit = requests_per_minute
        self.concurrent_limit = concurrent_requests
        self.max_query_bytes = max_query_bytes
        self.salt = secrets.token_bytes(32)
        self.requests: dict[str, deque[float]] = {}
        self.concurrent: dict[str, int] = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def _problem(
        request: Request, status: int, code: str, detail: str, retry: int | None = None
    ) -> Response:
        headers = {"Cache-Control": "no-store"}
        if retry is not None:
            headers["Retry-After"] = str(retry)
        return Response(
            status_code=status,
            media_type="application/problem+json",
            headers=headers,
            content=json.dumps(
                {
                    "type": f"https://carawaylabs.com/problems/{code.lower().replace('_', '-')}",
                    "title": {
                        400: "Invalid request",
                        413: "Payload too large",
                        414: "URI too long",
                        429: "Too many requests",
                    }[status],
                    "status": status,
                    "detail": detail,
                    "instance": request.url.path,
                    "request_id": getattr(request.state, "request_id", "unavailable"),
                    "code": code,
                }
            ),
        )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if request.method != "GET" or not (
            path in _PUBLIC_COLLECTIONS or path.startswith(_PUBLIC_DETAILS)
        ):
            return await call_next(request)
        if len(request.scope.get("query_string", b"")) > self.max_query_bytes:
            return self._problem(request, 414, "INVALID_REQUEST", "Query string is too long.")
        if (
            request.headers.get("transfer-encoding")
            or request.headers.get("content-length", "0") != "0"
        ):
            return self._problem(
                request, 413, "INVALID_REQUEST", "GET request bodies are unsupported."
            )
        repeated = {
            key for key in request.query_params if len(request.query_params.getlist(key)) > 1
        }
        allowed_repeated = (
            {"geography_id", "stratification"} if path == "/v1/observations" else set()
        )
        if repeated - allowed_repeated:
            return self._problem(
                request, 400, "INVALID_REQUEST", "Repeated query parameters are unsupported."
            )
        client = request.headers.get("do-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        key = hashlib.sha256(self.salt + client.encode()).hexdigest()
        now = time.monotonic()
        async with self.lock:
            if len(self.requests) > 10_000:
                self.requests = {
                    k: window
                    for k, window in self.requests.items()
                    if window and now - window[-1] < 60
                }
            window = self.requests.setdefault(key, deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= self.limit or self.concurrent.get(key, 0) >= self.concurrent_limit:
                retry = (
                    max(1, math.ceil(60 - (now - window[0]))) if len(window) >= self.limit else 1
                )
                logger.info(
                    "public_read_rejected", extra={"context": {"reason": "rate_or_concurrency"}}
                )
                return self._problem(
                    request, 429, "RATE_LIMITED", "Public request limit reached.", retry
                )
            window.append(now)
            self.concurrent[key] = self.concurrent.get(key, 0) + 1
        try:
            response = await call_next(request)
        finally:
            async with self.lock:
                remaining = self.concurrent[key] - 1
                if remaining:
                    self.concurrent[key] = remaining
                else:
                    del self.concurrent[key]
        # Detail resources have stable release/version-bearing representations.
        # Conditional requests save response transfer; shared caches honor max-age.
        if response.status_code == 200 and path.startswith(_PUBLIC_DETAILS):
            chunks = [chunk async for chunk in cast(StreamingResponse, response).body_iterator]
            body = b"".join(chunk.encode() if isinstance(chunk, str) else chunk for chunk in chunks)
            etag = '"' + hashlib.sha256(body).hexdigest() + '"'
            headers = dict(response.headers)
            headers["ETag"] = etag
            if etag in [
                item.strip() for item in request.headers.get("if-none-match", "").split(",")
            ]:
                logger.info(
                    "public_read_conditional_hit", extra={"context": {"resource": "detail"}}
                )
                return Response(
                    status_code=304,
                    headers={
                        "ETag": etag,
                        "Cache-Control": headers.get(
                            "cache-control", "public, max-age=60, must-revalidate"
                        ),
                    },
                )
            headers.pop("content-length", None)
            return Response(
                content=body, status_code=200, headers=headers, media_type=response.media_type
            )
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied_id = request.headers.get("x-request-id", "")
        request_id = getattr(request.state, "request_id", None) or normalize_request_id(supplied_id)
        request.state.request_id = request_id
        started_at = time.perf_counter()
        chat_route = request.url.path in {
            "/v1/knowledge-graph/chat", "/v1/assistant/mixed",
            "/v1/assistant/structured",
        }
        failure_type: str | None = None
        try:
            with request_correlation(request_id):
                response = await call_next(request)
        except asyncio.CancelledError:
            span = getattr(request.state, "request_span", None)
            dimensions = request_dimensions(request.method, _log_route(request), 500, "cancelled")
            dimensions["outcome"] = "cancelled"
            dimensions.pop("status_code")
            dimensions["status_class"] = "unknown"
            enrich_request(span, request_id, dimensions)
            emit_completion(logger, "api_request_failed", {
                **dimensions, "request_id": request_id,
                "duration_ms": round((time.perf_counter() - started_at) * 1000),
            })
            raise
        except Exception as exc:
            failure_type = type(exc).__name__
            request.state.request_failure_class = "unhandled_error"
            if not chat_route:
                emit_completion(
                    logger, "api_request_failed", {
                        "request_id": request_id,
                        "duration_ms": round((time.perf_counter() - started_at) * 1000),
                        **request_dimensions(request.method, _log_route(request), 500,
                                             "unhandled_error"),
                    },
                )
                raise
            # Catch before FastAPI's outer OTel middleware can record the raw
            # exception message/stack. Preserve the existing generic 500 body.
            response = Response("Internal Server Error", status_code=500)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        dimensions = request_dimensions(
            request.method, _log_route(request), response.status_code,
            "unhandled_error" if failure_type else "none",
        )
        request.state.request_dimensions = dimensions
        enrich_request(getattr(request.state, "request_span", None), request_id, dimensions)
        if chat_route:
            diagnostics = getattr(request.state, "knowledge_chat_diagnostics", {})
            outcome = diagnostics.get("outcome")
            if failure_type and outcome == "answered":
                outcome = "response_serialization_failure"
            if outcome is None:
                outcome = {
                    422: "request_validation_failure",
                    429: "rate_limited",
                    503: "route_unavailable",
                }.get(response.status_code, "unhandled_error")
            operational = assistant_operational_outcome(
                outcome, diagnostics.get("service_outcome") or diagnostics.get("outcome"),
                diagnostics.get("structured_cause"),
            )
            emit_completion(logger, "knowledge_chat_total", {
                **diagnostics,
                **dimensions,
                "request_id": request_id,
                "http_status": response.status_code,
                "duration_ms": round((time.perf_counter() - started_at) * 1000),
                "outcome": outcome,
                "operational_outcome": operational,
                "failure_type": failure_type,
            })
            return response
        emit_completion(
            logger, "api_request_completed", {
                    "request_id": request_id,
                    **dimensions,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000),
            },
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, requests_per_minute: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limit = requests_per_minute
        self.salt = secrets.token_bytes(32)
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith("/health/"):
            return await call_next(request)
        client = request.headers.get("do-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        now = time.monotonic()
        key = hashlib.sha256(self.salt + client.encode()).hexdigest()
        if len(self.requests) > 10_000:
            self.requests = defaultdict(
                deque,
                {
                    k: values
                    for k, values in self.requests.items()
                    if values and now - values[-1] < 60
                },
            )
        window = self.requests[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.limit:
            problem = {
                "type": "about:blank",
                "title": "Too Many Requests",
                "status": 429,
                "detail": "Request limit exceeded.",
                "instance": "rate-limit",
                "request_id": getattr(request.state, "request_id", "unavailable"),
            }
            return Response(
                status_code=429,
                media_type="application/problem+json",
                content=json.dumps(problem),
                headers={
                    "Retry-After": str(max(1, math.ceil(60 - (now - window[0])))),
                    "Cache-Control": "no-store",
                },
            )
        window.append(now)
        return await call_next(request)


class KnowledgeChatLimitMiddleware(BaseHTTPMiddleware):
    """Single-instance assistant limiter with route-scoped budgets."""

    def __init__(self, app: object) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.requests: dict[str, deque[float]] = defaultdict(deque)
        self.concurrent: dict[str, int] = defaultdict(int)
        self.literature_requests: deque[float] = deque()
        self.literature_concurrent = 0
        self.lock = asyncio.Lock()

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path not in {"/v1/knowledge-graph/chat", "/v1/assistant/mixed",
                        "/v1/assistant/structured"}:
            return await call_next(request)
        client = request.headers.get("do-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        # The two routes capable of paid literature calls share one bucket.
        scope = "structured" if path == "/v1/assistant/structured" else "literature"
        key = hashlib.sha256(f"{scope}:{client}".encode()).hexdigest()
        limit = 30 if path == "/v1/assistant/structured" else 10
        now = time.monotonic()
        paid = scope == "literature"
        async with self.lock:
            if len(self.requests) > 10_000:
                self.requests = defaultdict(
                    deque,
                    {k: values for k, values in self.requests.items()
                     if values and now - values[-1] < 600},
                )
            window = self.requests[key]
            while window and now - window[0] > 600:
                window.popleft()
            while self.literature_requests and now - self.literature_requests[0] > 600:
                self.literature_requests.popleft()
            rate_full = len(window) >= limit or (paid and len(self.literature_requests) >= 60)
            busy = self.concurrent.get(key, 0) >= 3 or (
                paid and self.literature_concurrent >= 6
            )
            if rate_full or busy:
                retry_after = "600" if rate_full else "1"
                problem = {
                    "type": "https://carawaylabs.com/problems/knowledge-chat-rate-limit",
                    "title": "Too many requests",
                    "status": 429,
                    "detail": "The evidence chat request limit has been reached.",
                    "instance": request.url.path,
                    "request_id": getattr(request.state, "request_id", "unavailable"),
                }
                return Response(
                    status_code=429,
                    media_type="application/problem+json",
                    content=json.dumps(problem),
                    headers={"Retry-After": retry_after},
                )
            window.append(now)
            self.concurrent[key] += 1
            if paid:
                self.literature_requests.append(now)
                self.literature_concurrent += 1
        try:
            return await call_next(request)
        finally:
            async with self.lock:
                self.concurrent[key] -= 1
                if self.concurrent[key] == 0:
                    del self.concurrent[key]
                if paid:
                    self.literature_concurrent -= 1


class PrivacyRequestLimitMiddleware(BaseHTTPMiddleware):
    """Five privacy-request mutations per ten minutes per connecting address."""

    def __init__(self, app: object) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not path.startswith("/v1/me/privacy-requests") or request.method not in {
            "POST",
            "GET",
        }:
            return await call_next(request)
        if request.method == "GET" and path.rstrip("/").endswith("/export"):
            mutating = False
        else:
            mutating = request.method == "POST"
        if not mutating:
            return await call_next(request)
        client = request.headers.get("do-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        key = hashlib.sha256(client.encode()).hexdigest()
        now = time.monotonic()
        window = self.requests[key]
        while window and now - window[0] > 600:
            window.popleft()
        if len(window) >= 5:
            problem = {
                "type": "https://carawaylabs.com/problems/privacy-request-rate-limit",
                "title": "Too many requests",
                "status": 429,
                "detail": "The privacy-request limit has been reached.",
                "instance": path,
                "request_id": getattr(request.state, "request_id", "unavailable"),
            }
            return Response(
                status_code=429,
                media_type="application/problem+json",
                content=json.dumps(problem),
                headers={"Retry-After": "600"},
            )
        window.append(now)
        return await call_next(request)


class FeedbackLimitMiddleware(BaseHTTPMiddleware):
    """Five POST /v1/feedback requests per ten minutes per hashed connecting address.

    In-memory and single-process only — not a distributed limiter. The deployment
    topology test is what blocks a second API instance or worker while this
    process-local window remains the abuse control.
    """

    _MAX_BODY_BYTES = 8192
    _WINDOW_SECONDS = 600
    _MAX_REQUESTS = 5

    def __init__(self, app: object) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path != "/v1/feedback" or request.method != "POST":
            return await call_next(request)

        request_id = getattr(request.state, "request_id", "unavailable")
        content_type = request.headers.get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            logger.info(
                "feedback_submission",
                extra={
                    "context": {
                        "outcome": "rejected",
                        "request_id": request_id,
                    }
                },
            )
            problem = {
                "type": "https://carawaylabs.com/problems/feedback-unsupported-media-type",
                "title": "Unsupported Media Type",
                "status": 415,
                "detail": "Feedback submissions must use application/json.",
                "instance": request.url.path,
                "request_id": request_id,
            }
            return Response(
                status_code=415,
                media_type="application/problem+json",
                content=json.dumps(problem),
                headers={"Cache-Control": "no-store"},
            )

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                length = -1
            if length < 0 or length > self._MAX_BODY_BYTES:
                logger.info(
                    "feedback_submission",
                    extra={
                        "context": {
                            "outcome": "rejected",
                            "request_id": request_id,
                        }
                    },
                )
                problem = {
                    "type": "https://carawaylabs.com/problems/feedback-payload-too-large",
                    "title": "Payload Too Large",
                    "status": 413,
                    "detail": "Feedback submissions must be at most 8192 bytes.",
                    "instance": request.url.path,
                    "request_id": request_id,
                }
                return Response(
                    status_code=413,
                    media_type="application/problem+json",
                    content=json.dumps(problem),
                    headers={"Cache-Control": "no-store"},
                )

        client = request.headers.get("do-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        key = hashlib.sha256(client.encode()).hexdigest()
        now = time.monotonic()
        window = self.requests[key]
        while window and now - window[0] > self._WINDOW_SECONDS:
            window.popleft()
        if len(window) >= self._MAX_REQUESTS:
            retry_after = max(1, int(self._WINDOW_SECONDS - (now - window[0])) + 1)
            logger.info(
                "feedback_submission",
                extra={
                    "context": {
                        "outcome": "throttled",
                        "request_id": request_id,
                    }
                },
            )
            problem = {
                "type": "https://carawaylabs.com/problems/feedback-rate-limit",
                "title": "Too many requests",
                "status": 429,
                "detail": "The feedback submission limit has been reached.",
                "instance": request.url.path,
                "request_id": request_id,
            }
            return Response(
                status_code=429,
                media_type="application/problem+json",
                content=json.dumps(problem),
                headers={
                    "Retry-After": str(retry_after),
                    "Cache-Control": "no-store",
                },
            )
        window.append(now)
        return await call_next(request)
