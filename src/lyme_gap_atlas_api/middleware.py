import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:128]
        request.state.request_id = request_id
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(
                "api_request_failed",
                extra={
                    "context": {
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "failure_type": type(exc).__name__,
                    }
                },
            )
            raise
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        logger.info(
            "api_request_completed",
            extra={
                "context": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000),
                }
            },
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, requests_per_minute: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limit = requests_per_minute
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
        window = self.requests[client]
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
            )
        window.append(now)
        return await call_next(request)


class KnowledgeChatLimitMiddleware(BaseHTTPMiddleware):
    """Single-instance v1 limiter: ten requests/ten minutes and three concurrent/IP."""

    def __init__(self, app: object) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.requests: dict[str, deque[float]] = defaultdict(deque)
        self.concurrent: dict[str, int] = defaultdict(int)
        self.lock = asyncio.Lock()

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path != "/v1/knowledge-graph/chat":
            return await call_next(request)
        client = request.headers.get("do-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        key = hashlib.sha256(client.encode()).hexdigest()
        now = time.monotonic()
        async with self.lock:
            window = self.requests[key]
            while window and now - window[0] > 600:
                window.popleft()
            if len(window) >= 10 or self.concurrent[key] >= 3:
                retry_after = "600" if len(window) >= 10 else "1"
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
        try:
            return await call_next(request)
        finally:
            async with self.lock:
                self.concurrent[key] -= 1


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
