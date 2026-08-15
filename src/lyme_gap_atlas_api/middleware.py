import json
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:128]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
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
