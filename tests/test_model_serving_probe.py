"""Service access proof must reject personal identities and incomplete releases."""

import asyncio
import json
import logging

import pytest
from snowflake.connector.errors import ProgrammingError

from lyme_gap_atlas_api import model_serving_probe as probe
from lyme_gap_atlas_api.config import ApiSettings


def settings():
    return ApiSettings(
        snowflake_account="fixture-secret",
        snowflake_user="OH_LYME_API_SVC",
        snowflake_role="OH_LYME_PROD_READ",
        snowflake_database="ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        snowflake_warehouse="COMPUTE_WH",
    )


class Session:
    def __init__(self, fault):
        self.fault = fault
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self

    def execute(self, sql, parameters=(), *, timeout):
        assert timeout == 5 and sql.startswith("SELECT")
        self.calls.append(sql)
        if "CURRENT_USER" in sql:
            self.rows = [
                (
                    "OH_LYME_API_SVC",
                    "OH_LYME_PROD_READ",
                    "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
                    "COMPUTE_WH",
                    False,
                    False,
                    self.fault == "elevated",
                )
            ]
            if self.fault == "personal":
                self.rows[0] = ("HUMAN_PAT", *self.rows[0][1:])
        elif "CURRENT_RELEASE_V" in sql:
            self.rows = [(probe.INPUT_RELEASE, probe.INPUT_BUNDLE)]
            if self.fault == "release":
                self.rows *= 2
        elif "GROUP BY model_id" in sql:
            assert parameters == (probe.INPUT_RELEASE, probe.INPUT_BUNDLE) * 2
            self.rows = [(model, 3144, 3144, 0) for model in sorted(probe.MODELS)]
            if self.fault == "missing":
                self.rows.pop()
            elif self.fault == "duplicate":
                self.rows[1] = self.rows[0]
            elif self.fault in {"identity", "count", "duplicate_county"}:
                model = self.rows[0][0]
                self.rows[0] = (
                    model,
                    3143 if self.fault == "count" else 3144,
                    3143 if self.fault == "duplicate_county" else 3144,
                    1 if self.fault == "identity" else 0,
                )
            elif self.fault == "denied":
                raise ProgrammingError(msg="fixture-secret", errno=2003, sqlstate="42S02")
            elif self.fault == "timeout":
                raise ProgrammingError(msg="fixture-secret", errno=604, sqlstate="57014")
        elif "FEATURE_STORE" in sql:
            if self.fault == "base_access":
                self.rows = []
            elif self.fault == "wrong_denial":
                raise RuntimeError("fixture-secret")
            elif self.fault == "schema_denial":
                raise ProgrammingError(msg="fixture-secret", errno=2003, sqlstate="02000")
            elif self.fault == "no_data_other_error":
                raise ProgrammingError(msg="fixture-secret", errno=9999, sqlstate="02000")
            elif self.fault == "unrelated_sqlstate":
                raise ProgrammingError(msg="fixture-secret", errno=2003, sqlstate="HY000")
            else:
                raise ProgrammingError(msg="fixture-secret", errno=2003, sqlstate="42S02")
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "personal",
        "elevated",
        "release",
        "missing",
        "duplicate",
        "identity",
        "count",
        "duplicate_county",
        "denied",
        "timeout",
        "base_access",
        "wrong_denial",
        "schema_denial",
        "no_data_other_error",
        "unrelated_sqlstate",
    ],
)
def test_service_model_and_denial_boundaries(monkeypatch, fault):
    session = Session(fault)
    monkeypatch.setattr(probe, "connect", lambda _: session)
    result = probe.probe_models(settings())
    assert result["publication_matches"] == (fault in {None, "schema_denial"})
    assert len(session.calls) <= 7
    assert "fixture-secret" not in json.dumps(result)
    if fault in {"personal", "elevated"}:
        assert len(session.calls) == 1
    if fault in {None, "schema_denial"}:
        assert result["base_tables_inaccessible"] == 4
        assert result["county_rows"] == 6288
    if fault == "timeout":
        assert result["read_status"] == "query_canceled"


def test_startup_dispatch_once_and_fixture_apps_inert(monkeypatch):
    from lyme_gap_atlas_api import app as app_module

    calls = []
    monkeypatch.setattr(app_module, "start_model_serving_probe", lambda _: calls.append(True))
    monkeypatch.setattr(app_module, "start_reader_probe", lambda _: None)
    monkeypatch.setattr(app_module, "start_county_context_probe", lambda _: None)
    application = app_module.create_app(settings=settings())
    application.openapi()
    assert calls == []

    async def exercise():
        for _ in range(2):
            async with application.router.lifespan_context(application):
                assert len(calls) == 1
        fixture = app_module.create_app(repository=object(), settings=settings())
        async with fixture.router.lifespan_context(fixture):
            assert len(calls) == 1

    asyncio.run(exercise())


def test_unconfigured_and_dev_are_not_probed(monkeypatch):
    monkeypatch.setattr(probe, "Thread", lambda **_: pytest.fail("Unexpected dispatch"))
    probe.start_model_serving_probe(ApiSettings())
    assert (
        probe.probe_models(
            settings().model_copy(
                update={
                    "snowflake_presentation_database": "ONE_HEALTH_LYME_GAP_ATLAS_DEV",
                    "snowflake_database": "ONE_HEALTH_LYME_GAP_ATLAS_DEV",
                }
            )
        )["identity_status"]
        == "unsupported_target"
    )


def test_log_and_background_dispatch_use_existing_settings_without_secrets(monkeypatch, caplog):
    supplied = settings()
    session = Session("denied")
    monkeypatch.setattr(probe, "connect", lambda config: session)
    with caplog.at_level(logging.INFO):
        probe._log(supplied)
    record = caplog.records[-1]
    assert record.message == probe.EVENT
    assert record.context["read_status"] == "object_or_access_unavailable"
    assert not record.context["publication_matches"]
    assert "fixture-secret" not in caplog.text and not record.exc_info
    calls = []

    class Worker:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def start(self):
            calls.append("started")

    monkeypatch.setattr(probe, "Thread", Worker)
    probe.start_model_serving_probe(supplied)
    assert calls == [{"target": probe._log, "args": (supplied,), "daemon": True}, "started"]


def test_dispatch_failure_never_blocks_startup_or_exposes_driver_text(monkeypatch, caplog):
    def unavailable(**kwargs):
        raise RuntimeError("fixture-secret")

    monkeypatch.setattr(probe, "Thread", unavailable)
    with caplog.at_level(logging.INFO):
        probe.start_model_serving_probe(settings())
    record = caplog.records[-1]
    assert record.context == {"identity_status": "dispatch_unavailable"}
    assert "fixture-secret" not in caplog.text and not record.exc_info
