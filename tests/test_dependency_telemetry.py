"""Actual SDK boundaries preserve lifecycle, nesting, failures and privacy."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lyme_gap_atlas_api import dependency_telemetry as telemetry
from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.auth import _ObservedJWKClient
from lyme_gap_atlas_api.auth_admin import SupabaseAuthAdmin
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.privacy_requests import SupabasePrivacyRequestStore
from lyme_gap_atlas_api.profiles import SupabaseProfileStore

SECRET = "private-SQL-body-prompt-credential-198.51.100.10"


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    return exporter


class Resource:
    def __init__(self, failure=None):
        self.failure = failure
        self.exits = []
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exits.append(args)

    def cursor(self):
        return self

    def execute(self, *args, **kwargs):
        self.executions.append((args, kwargs))
        if self.failure:
            raise self.failure
        return self

    def fetchall(self):
        return [(SECRET,)]

    def fetchone(self):
        return (SECRET,)

    def close(self):
        return None


@pytest.mark.parametrize(
    "failure,category",
    [(None, "none"), (RuntimeError(SECRET), "dependency_error"), (TimeoutError(SECRET), "timeout")],
)
def test_snowflake_actual_calls(spans, monkeypatch, failure, category):
    resource = Resource(failure)
    monkeypatch.setattr(telemetry, "shared_connect", lambda *args, **kwargs: resource)
    with trace.get_tracer("test").start_as_current_span("request") as parent:

        def run():
            with telemetry.connect(ApiSettings()) as connection, connection.cursor() as cursor:
                cursor.execute(SECRET, (SECRET,), timeout=7)
                assert cursor.fetchall() == [(SECRET,)]

        if failure:
            with pytest.raises(type(failure)) as captured:
                run()
            assert captured.value is failure
        else:
            run()
    assert len(resource.exits) == 2
    assert resource.executions == [((SECRET, (SECRET,)), {"timeout": 7})]
    finished = spans.get_finished_spans()
    dependencies = [s for s in finished if s.name.startswith("atlas.dependency.")]
    assert len([s for s in dependencies if s.name.endswith(".execute")]) == 1
    for span in dependencies:
        assert span.parent.span_id == parent.get_span_context().span_id
        assert span.start_time >= finished[-1].start_time
        assert span.end_time <= finished[-1].end_time
    execute = next(s for s in dependencies if s.name.endswith(".execute"))
    assert execute.attributes["atlas.dependency.failure_class"] == category
    assert execute.attributes["atlas.dependency.outcome"] == ("failure" if failure else "success")
    assert SECRET not in json.dumps([dict(s.attributes) for s in finished])
    assert all(not s.events and not s.status.description for s in dependencies)


def test_unknown_names_are_bounded_and_original_error_survives(spans):
    error = ValueError(SECRET)
    with pytest.raises(ValueError) as captured, telemetry.dependency_span(SECRET, SECRET):
        raise error
    assert captured.value is error
    span = spans.get_finished_spans()[0]
    assert span.name == "atlas.dependency.other.other"
    assert SECRET not in str(span.attributes)


def test_span_start_failure_preserves_call(monkeypatch):
    class BrokenTracer:
        def start_span(self, *args, **kwargs):
            raise RuntimeError(SECRET)

    monkeypatch.setattr(trace, "get_tracer", lambda *args: BrokenTracer())
    with telemetry.dependency_span("snowflake", "execute"):
        result = 42
    assert result == 42


def test_public_route_dependency_is_child_of_server(spans, monkeypatch):
    resource = Resource()
    resource.fetchall = lambda: []
    monkeypatch.setattr(telemetry, "shared_connect", lambda *args, **kwargs: resource)
    client = TestClient(create_app(settings=ApiSettings()))
    response = client.get("/v1/indicators")
    # Successful I/O with no admitted metadata retains the existing fail-closed 503.
    assert response.status_code == 503
    finished = spans.get_finished_spans()
    root = next(s for s in finished if s.kind == trace.SpanKind.SERVER)
    dependencies = [s for s in finished if s.name.startswith("atlas.dependency.")]
    assert len([s for s in dependencies if s.name.endswith(".execute")]) == 2
    assert all(s.parent.span_id == root.context.span_id for s in dependencies)


@pytest.mark.parametrize(
    "adapter,operation",
    [
        (SupabaseProfileStore, "profile_request"),
        (SupabasePrivacyRequestStore, "privacy_request"),
        (SupabaseAuthAdmin, "auth_admin_request"),
    ],
)
@pytest.mark.parametrize("status", [200, 503])
def test_supabase_boundaries(spans, adapter, operation, status):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"private": SECRET})
        )
    )
    store = adapter(
        ApiSettings(supabase_url="https://example.invalid", supabase_secret_key="test-placeholder"),
        client,
    )

    def run():
        if adapter is SupabaseAuthAdmin:
            return store._request("GET", f"https://example.invalid/{SECRET}")
        return store._request("GET", params={"user_id": SECRET})

    if status == 503:
        with pytest.raises(RuntimeError):
            run()
    else:
        assert run().json() == {"private": SECRET}
    dependency = spans.get_finished_spans()[0]
    assert dependency.attributes["atlas.dependency.operation"] == operation
    assert dependency.attributes["atlas.dependency.outcome"] == (
        "success" if status == 200 else "failure"
    )
    assert SECRET not in str(dependency.attributes)


def test_jwks_refresh_boundary(spans, monkeypatch):
    monkeypatch.setattr("jwt.PyJWKClient.fetch_data", lambda self: {"private": SECRET})
    assert _ObservedJWKClient("https://example.invalid").fetch_data() == {"private": SECRET}
    span = spans.get_finished_spans()[0]
    assert span.attributes["atlas.dependency.operation"] == "jwks_fetch"
    assert SECRET not in str(span.attributes)


def test_optional_attribute_and_end_failure(monkeypatch):
    class BrokenSpan(trace.NonRecordingSpan):
        def set_attribute(self, *args):
            raise RuntimeError(SECRET)

        def end(self, *args):
            raise RuntimeError(SECRET)

    class Tracer:
        def start_span(self, *args, **kwargs):
            return BrokenSpan(trace.INVALID_SPAN_CONTEXT)

    monkeypatch.setattr(trace, "get_tracer", lambda *args: Tracer())
    with telemetry.dependency_span("snowflake", "execute"):
        assert True


def test_proxy_preserves_other_sdk_methods(spans, monkeypatch):
    resource = Resource()
    resource.version = SECRET
    monkeypatch.setattr(telemetry, "shared_connect", lambda *args, **kwargs: resource)
    with telemetry.connect(ApiSettings()) as connection:
        assert connection.version == SECRET
        with connection.cursor() as cursor:
            assert cursor.fetchone() == (SECRET,)
        connection.close()
    assert SECRET not in str([s.attributes for s in spans.get_finished_spans()])
