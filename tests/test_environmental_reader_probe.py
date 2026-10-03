"""Service identity/read proof never exposes connection values or SDK errors."""

import asyncio
import logging

import pytest

from lyme_gap_atlas_api import environmental_reader_probe as module
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.environmental_context import MEASURES

SECRET = "private-host-password-query-detail"


def settings(**changes):
    return ApiSettings(
        snowflake_account=SECRET,
        snowflake_user="OH_LYME_API_SVC",
        snowflake_role="OH_LYME_PROD_READ",
        snowflake_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        snowflake_warehouse="COMPUTE_WH",
        **changes,
    )


class Session:
    def __init__(self, identity=None, metadata=None, observations=None, denied=False):
        self.identity = identity or (
            "OH_LYME_API_SVC",
            "OH_LYME_PROD_READ",
            "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
            "COMPUTE_WH",
        )
        self.metadata = metadata or []
        self.observations = observations or []
        self.denied = denied
        self.calls = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self

    def execute(self, query, *, timeout):
        assert timeout == 5
        self.calls.append(query)
        assert query.startswith("SELECT") and "LIMIT" in query or "CURRENT_USER" in query
        if "CURRENT_USER" in query:
            self.rows = [self.identity]
        elif self.denied:
            error = RuntimeError(SECRET)
            error.errno = 2003
            raise error
        elif "METADATA_V" in query:
            self.rows = self.metadata
        else:
            self.rows = self.observations

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


def test_actual_identity_and_empty_views_are_separate_from_publication(monkeypatch):
    session = Session()
    monkeypatch.setattr(module, "connect", lambda supplied: session)
    report = module.probe_reader(settings())
    assert report["identity_status"] == "verified"
    assert report["metadata_read"] == report["observations_read"] == "readable_empty"
    assert not report["publication_matches"] and len(session.calls) == 3
    assert "OH_LYME_API_SVC" not in str(report) and SECRET not in str(report)


def test_denied_visibility_is_not_missing_object_and_never_logs_error(monkeypatch, caplog):
    session = Session(denied=True)
    monkeypatch.setattr(module, "connect", lambda supplied: session)
    with caplog.at_level(logging.INFO):
        module.log_reader_probe(settings())
    report = caplog.records[-1].context
    assert report["identity_status"] == "verified"
    assert report["metadata_read"] == "object_or_access_unavailable"
    assert report["observations_read"] == "object_or_access_unavailable"
    assert SECRET not in caplog.text and SECRET not in str(report)
    assert not caplog.records[-1].exc_info


@pytest.mark.parametrize("field", range(4))
def test_unexpected_identity_never_reads_views(monkeypatch, field):
    identity = list(Session().identity)
    identity[field] = SECRET
    session = Session(identity=tuple(identity))
    monkeypatch.setattr(module, "connect", lambda supplied: session)
    report = module.probe_reader(settings())
    assert report["identity_status"] == "mismatch" and len(session.calls) == 1
    assert SECRET not in str(report)


def test_no_target_or_connection_failure_exposes_configuration(monkeypatch):
    def denied(supplied):
        raise TimeoutError(SECRET)

    monkeypatch.setattr(module, "connect", denied)
    assert module.probe_reader(settings())["connection_status"] == "timeout"
    unsupported = settings(snowflake_presentation_database=SECRET)
    assert module.probe_reader(unsupported)["identity_status"] == "unsupported_target"


@pytest.mark.parametrize("mixed", [False, True])
def test_same_release_count_proof_does_not_log_release_ids(monkeypatch, mixed):
    metadata = [(SECRET, measure) for measure in MEASURES]
    session = Session(metadata=metadata, observations=[("different" if mixed else SECRET,)])
    monkeypatch.setattr(module, "connect", lambda supplied: session)
    report = module.probe_reader(settings())
    assert report["publication_matches"] is (not mixed)
    assert SECRET not in str(report) and "different" not in str(report)


def test_dispatch_is_daemon_and_never_needs_activation_or_secret_reads(monkeypatch):
    calls = []

    class Worker:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def start(self):
            calls.append("started")

    monkeypatch.setattr(module, "Thread", Worker)
    module.start_reader_probe(ApiSettings())
    assert calls == []
    configured = settings()
    assert not configured.environmental_context_enabled
    module.start_reader_probe(configured)
    assert calls[0] == {"target": module.log_reader_probe, "args": (configured,), "daemon": True}
    assert calls[1] == "started"


def test_invalid_identity_and_generic_failure_remain_private(monkeypatch):
    session = Session(identity=(None, SECRET, SECRET, SECRET))
    monkeypatch.setattr(module, "connect", lambda supplied: session)
    assert module.probe_reader(settings())["identity_status"] == "invalid_identity"
    assert len(session.calls) == 1

    def denied(supplied):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(module, "connect", denied)
    report = module.probe_reader(settings())
    assert report["connection_status"] == "dependency_unavailable"
    assert SECRET not in str(report)


def test_background_dispatch_failure_does_not_break_app_or_log_error(monkeypatch, caplog):
    def denied(**kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(module, "Thread", denied)
    with caplog.at_level(logging.INFO):
        module.start_reader_probe(settings())
    assert caplog.records[-1].context == {"identity_status": "dispatch_unavailable"}
    assert SECRET not in caplog.text and not caplog.records[-1].exc_info


def test_app_construction_and_exports_are_inert_and_startup_dispatches_once(monkeypatch):
    import importlib

    from lyme_gap_atlas_api import app as app_module

    calls = []
    monkeypatch.setattr(module, "start_reader_probe", lambda config: calls.append(config))
    # Reload executes the module-level factory with configured credentials.
    monkeypatch.setattr(app_module, "get_settings", settings)
    monkeypatch.setattr("lyme_gap_atlas_api.config.get_settings", settings)
    app_module = importlib.reload(app_module)
    assert calls == []
    application = app_module.create_app(settings=settings())
    application.openapi()
    application.first_party_openapi()
    assert calls == []

    async def exercise():
        async with application.router.lifespan_context(application):
            assert len(calls) == 1
        async with application.router.lifespan_context(application):
            assert len(calls) == 1
        fixture = app_module.create_app(repository=object(), settings=settings())
        async with fixture.router.lifespan_context(fixture):
            assert len(calls) == 1

    asyncio.run(exercise())
