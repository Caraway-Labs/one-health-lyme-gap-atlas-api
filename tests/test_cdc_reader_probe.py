"""Probe control/serialization tests; mock reads are not service acceptance."""

import json

import pytest

from lyme_gap_atlas_api import cdc_reader_probe

SERVICE = ("OH_LYME_API_SVC", "OH_LYME_PROD_READ", "ONE_HEALTH_LYME_GAP_ATLAS_PROD", "COMPUTE_WH")


class Cursor:
    def __init__(self, identity):
        self.identity = identity
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, *args, **kwargs):
        self.statements.append((sql, kwargs))

    def fetchone(self):
        return self.identity


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return self._cursor


class ProducerCursor(Cursor):
    def __init__(self, identity=SERVICE, authority_timeout=False):
        super().__init__(identity)
        self.authority_timeout = authority_timeout
        self.description = []
        self.rows = []

    def execute(self, sql, *args, **kwargs):
        from lyme_gap_atlas_data import cdc_consumer_runtime

        super().execute(sql, *args, **kwargs)
        if sql == cdc_consumer_runtime.IDENTITY_SQL:
            self.description = [(key,) for key in ("USER", "ROLE", "DATABASE", "WAREHOUSE")]
            self.rows = [self.identity]
        elif sql == cdc_consumer_runtime.AUTHORITY_SQL:
            if self.authority_timeout:
                raise TimeoutError("private SQL and account diagnostics")
            self.description, self.rows = [], []

    def fetchmany(self, limit):
        return self.rows[:limit]


def test_service_probe_disables_secondary_roles_and_emits_only_safe_summary(monkeypatch, capsys):
    cursor = Cursor(SERVICE)
    monkeypatch.setattr(cdc_reader_probe, "connect", lambda _: Connection(cursor))
    calls = []

    def read(measure, county, **kwargs):
        calls.append((measure, county))
        return {
            "surveillance_evidence": {
                "normalized_state": "unknown" if county == "12047" else "detected"
            },
            "private_proof": "not serializable",
            "source_version_id": "private",
        }

    monkeypatch.setattr(cdc_reader_probe, "_read", read)
    cdc_reader_probe.main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["identity"] == dict(
        zip(("user", "role", "database", "warehouse"), SERVICE, strict=True)
    )
    assert calls == [
        ("scapularis_status", "13081"),
        ("burgdorferi_status", "12047"),
        ("burgdorferi_status", "19135"),
    ]
    assert [item["normalized_state"] for item in result["consumer_reads"]] == [
        "detected",
        "unknown",
        "detected",
    ]
    assert "private" not in output and "not serializable" not in output
    assert cursor.statements[0] == ("USE SECONDARY ROLES NONE", {})
    assert cursor.statements[1][1] == {"timeout": 15}


@pytest.mark.parametrize(
    "identity",
    [
        None,
        ("MATTHEWCARAWAY", *SERVICE[1:]),
        (SERVICE[0], "ACCOUNTADMIN", *SERVICE[2:]),
    ],
)
def test_service_probe_refuses_wrong_or_missing_actual_identity(monkeypatch, capsys, identity):
    monkeypatch.setattr(cdc_reader_probe, "connect", lambda _: Connection(Cursor(identity)))
    monkeypatch.setattr(
        cdc_reader_probe, "_read", lambda *_: pytest.fail("must not query producer")
    )
    with pytest.raises(SystemExit, match="identity verification failed"):
        cdc_reader_probe.main()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("stage", ["connection", "producer", "malformed"])
def test_probe_redacts_failures_without_partial_results(monkeypatch, capsys, stage):
    def fail(*args, **kwargs):
        raise RuntimeError("private SQL and account diagnostics")

    monkeypatch.setattr(
        cdc_reader_probe,
        "connect",
        fail if stage == "connection" else lambda _: Connection(Cursor(SERVICE)),
    )
    monkeypatch.setattr(
        cdc_reader_probe, "_read", (lambda *_, **kwargs: {}) if stage == "malformed" else fail
    )
    with pytest.raises(SystemExit, match="^CDC service acceptance probe unavailable$") as error:
        cdc_reader_probe.main()
    assert error.value.__suppress_context__ is True
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_pinned_producer_disables_secondary_roles_and_rechecks_its_own_session(monkeypatch):
    from lyme_gap_atlas_data import cdc_consumer_runtime

    from lyme_gap_atlas_api import cdc_surveillance
    from lyme_gap_atlas_api.config import ApiSettings

    cursor = ProducerCursor((SERVICE[0], "wrong-primary-role", *SERVICE[2:]))
    monkeypatch.setattr(cdc_surveillance, "connect", lambda _: Connection(cursor))
    config = ApiSettings(
        snowflake_auth_method="key_pair", snowflake_user=SERVICE[0], public_query_timeout_seconds=5
    )
    from lyme_gap_atlas_api.repository import AtlasDataUnavailableError

    with pytest.raises(AtlasDataUnavailableError, match="Governed CDC surveillance is unavailable"):
        cdc_surveillance._read("scapularis_status", "13081", settings=config)
    # Real pinned Data code runs here: no Data projector/admission method is mocked.
    assert len(cursor.statements) == 3
    assert cursor.statements[1] == ("USE SECONDARY ROLES NONE", {"timeout": 5})
    assert cursor.statements[2][0] == cdc_consumer_runtime.IDENTITY_SQL
