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

    def execute(self, sql, **kwargs):
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


def test_service_probe_disables_secondary_roles_and_emits_only_safe_summary(monkeypatch, capsys):
    cursor = Cursor(SERVICE)
    monkeypatch.setattr(cdc_reader_probe, "connect", lambda _: Connection(cursor))
    calls = []

    def read(measure, county):
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
