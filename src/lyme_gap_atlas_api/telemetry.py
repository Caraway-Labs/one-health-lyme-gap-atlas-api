"""Privacy-safe API request dimensions on the existing FastAPI server span."""

import re
import uuid
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

REQUEST_CORRELATION: ContextVar[str] = ContextVar("atlas_http_request_id", default="unavailable")

ROUTES = frozenset(
    {
        "/docs",
        "/docs/oauth2-redirect",
        "/health/live",
        "/health/ready",
        "/openapi.json",
        "/redoc",
        "/v1/atlas/geometry",
        "/v1/atlas/metadata",
        "/v1/atlas/ranking.csv",
        "/v1/atlas/scores",
        "/v1/counties/{fips}",
        "/v1/counties/{fips}/report.pdf",
        "/v1/feedback",
        "/v1/geographies/{geography_type}/{geography_id}",
        "/v1/indicators",
        "/v1/indicators/{id}",
        "/v1/indicators/{indicator_id}",
        "/v1/knowledge-graph/chat",
        "/v1/me/privacy-requests",
        "/v1/me/privacy-requests/{request_id}",
        "/v1/me/privacy-requests/{request_id}/confirm",
        "/v1/me/privacy-requests/{request_id}/export",
        "/v1/me/profile",
        "/v1/measures",
        "/v1/measures/{id}",
        "/v1/measures/{measure_id}",
        "/v1/methodologies/{id}",
        "/v1/methodologies/{methodology_id}",
        "/v1/observations",
        "/v1/sources",
        "/v1/sources/{id}",
        "/v1/sources/{source_id}",
        "/v1/states/{state}/report.pdf",
        "unmatched",
    }
)

METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"})
OUTCOMES = frozenset({"success", "client_error", "server_error", "cancelled"})
FAILURES = frozenset(
    {
        "none",
        "http_client_error",
        "http_server_error",
        "unhandled_error",
        "cancelled",
        "timeout",
        "dependency_error",
    }
)


def request_id(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value) else str(uuid.uuid4())


@contextmanager
def request_correlation(value: str) -> Any:
    token = REQUEST_CORRELATION.set(value)
    try:
        yield
    finally:
        REQUEST_CORRELATION.reset(token)


def request_dimensions(
    method: str, route: str, status: int, failure: str = "none"
) -> dict[str, Any]:
    """Callers supply a registered route template or the literal unmatched sentinel."""
    status = status if 100 <= status <= 599 else 500
    return {
        "method": method if method in METHODS else "OTHER",
        "path": route if route in ROUTES else "unmatched",
        "status_code": status,
        "status_class": f"{status // 100}xx",
        "outcome": "server_error"
        if status >= 500
        else "client_error"
        if status >= 400
        else "success",
        "failure_class": (failure if failure in FAILURES else "unhandled_error")
        if failure != "none"
        else "http_server_error"
        if status >= 500
        else "http_client_error"
        if status >= 400
        else "none",
    }


def enrich_request(span: Any, correlation: str, dimensions: dict[str, Any]) -> None:
    with suppress(Exception):
        span.set_attribute("request.id", correlation)
        span.set_attribute("http.route", dimensions["path"])
        span.set_attribute("http.method", dimensions["method"])
        if "status_code" in dimensions:
            span.set_attribute("http.status_code", dimensions["status_code"])
        for field in ("status_class", "outcome", "failure_class"):
            span.set_attribute(f"atlas.request.{field}", dimensions[field])
        span.set_attribute("atlas.telemetry.schema_version", "1")
        span.update_name(f"{dimensions['method']} {dimensions['path']}")


class _PrivateSpan(trace.Span):
    """Filter instrumentation before attributes/events reach the shared provider/exporter.

    FastAPI otherwise records raw URLs, network identity and exception text. Delegation
    preserves its canonical span/context/timing without changing the global provider.
    """

    def __init__(self, span: Any) -> None:
        self._span = span

    def __getattr__(self, name: str) -> Any:
        return getattr(self._span, name)

    def get_span_context(self) -> trace.SpanContext:
        return self._span.get_span_context()  # type: ignore[no-any-return]

    def is_recording(self) -> bool:
        return self._span.is_recording()  # type: ignore[no-any-return]

    def update_name(self, name: str) -> None:
        with suppress(Exception):
            self._span.update_name(name)

    def set_attribute(self, key: str, value: Any) -> None:
        value = _request_attribute(key, value)
        if value is not None:
            with suppress(Exception):
                self._span.set_attribute(key, value)

    def set_attributes(self, attributes: Any) -> None:
        for key, value in (attributes or {}).items():
            self.set_attribute(key, value)

    def record_exception(self, exception: BaseException, *args: Any, **kwargs: Any) -> None:
        self.set_attribute("atlas.request.failure_class", "unhandled_error")

    def add_event(self, *args: Any, **kwargs: Any) -> None:
        pass  # No automatic exception or header payloads.

    def set_status(self, status: Any, description: str | None = None) -> None:
        code = status.status_code if isinstance(status, Status) else status
        with suppress(Exception):
            self._span.set_status(Status(code))

    def end(self, *args: Any, **kwargs: Any) -> None:
        with suppress(Exception):
            self._span.end(*args, **kwargs)


class _PrivateTracer:
    def __init__(self, tracer: Any) -> None:
        self._tracer = tracer

    def start_span(self, name: str, *args: Any, **kwargs: Any) -> Any:
        attributes = kwargs.pop("attributes", None) or {}
        safe = {
            key: clean
            for key, value in attributes.items()
            if key in {"http.route", "http.method", "http.request.method"}
            and (clean := _request_attribute(key, value)) is not None
        }
        kwargs["record_exception"] = False
        kwargs["set_status_on_exception"] = False
        try:
            return _PrivateSpan(self._tracer.start_span(name, *args, attributes=safe, **kwargs))
        except Exception:
            return trace.INVALID_SPAN

    @contextmanager
    def start_as_current_span(self, name: str, *args: Any, **kwargs: Any) -> Any:
        end_on_exit = kwargs.pop("end_on_exit", True)
        span = self.start_span(name, *args, **kwargs)
        with trace.use_span(
            span, end_on_exit=end_on_exit, record_exception=False, set_status_on_exception=False
        ):
            yield span


class PrivateInstrumentationProvider(trace.TracerProvider):
    """Adapter exclusively for FastAPI; shared observability still owns tracing."""

    def get_tracer(self, *args: Any, **kwargs: Any) -> Any:
        return _PrivateTracer(trace.get_tracer_provider().get_tracer(*args, **kwargs))


def _request_attribute(key: str, value: Any) -> Any:
    enums = {
        "http.route": ROUTES,
        "http.method": METHODS | {"OTHER"},
        "http.request.method": METHODS | {"OTHER"},
        "atlas.request.status_class": {f"{i}xx" for i in range(1, 6)} | {"unknown"},
        "atlas.request.outcome": OUTCOMES,
        "atlas.request.failure_class": FAILURES,
        "atlas.telemetry.schema_version": {"1"},
    }
    if key in enums:
        if isinstance(value, str) and value in enums[key]:
            return value
        return "unmatched" if key == "http.route" else "OTHER" if "method" in key else None
    if key in {"http.status_code", "http.response.status_code"}:
        return value if type(value) is int and 100 <= value <= 599 else None
    if key == "request.id":
        return (
            value
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value)
            else None
        )
    if key == "error.type":
        return "http_error"  # Do not copy arbitrary semantic-convention exception/type payloads.
    return None


def server_request_hook(span: Any, scope: dict[str, Any]) -> None:
    with suppress(Exception):
        headers = dict(scope.get("headers", []))
        correlation = request_id(headers.get(b"x-request-id", b"").decode("latin-1"))
        scope.setdefault("state", {})["request_id"] = correlation
        scope["state"]["request_span"] = span
        span.set_attribute("request.id", correlation)


def server_response_hook(span: Any, scope: dict[str, Any], message: dict[str, Any]) -> None:
    with suppress(Exception):
        if message.get("type") != "http.response.start":
            return
        state = scope.get("state", {})
        root = state.get("request_span")
        if root is None:
            return
        route = getattr(scope.get("route"), "path", "unmatched")
        dimensions = state.get("request_dimensions") or request_dimensions(
            scope.get("method", "OTHER"),
            route,
            message["status"],
            state.get("request_failure_class", "none"),
        )
        enrich_request(root, state["request_id"], dimensions)
        headers = message.setdefault("headers", [])
        if not any(key.lower() == b"x-request-id" for key, _ in headers):
            headers.append((b"x-request-id", state["request_id"].encode("ascii")))
        if message["status"] >= 500:
            root.set_status(StatusCode.ERROR)
