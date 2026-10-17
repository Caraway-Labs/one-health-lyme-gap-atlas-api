"""Configured-principal checks and redacted, bounded decode proof."""

import json

import pytest
from test_county_context import FIXTURE, RELEASE, row

from lyme_gap_atlas_api import county_context_probe as probe
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
    identity = (
        "OH_LYME_API_SVC",
        "OH_LYME_PROD_READ",
        "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        "COMPUTE_WH",
    )

    def __init__(self, fault=None):
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
        self.calls.append((sql, parameters))
        if "CURRENT_USER" in sql:
            self.rows = [
                self.identity if self.fault != "identity" else ("HUMAN_PAT", *self.identity[1:])
            ]
        elif "CURRENT_RELEASE_V" in sql:
            self.rows = [(RELEASE,)] if self.fault != "release" else []
        else:
            if self.fault == "denied":
                error = RuntimeError("fixture-secret")
                error.errno = 2003
                raise error
            self.rows = [row(r["measure_id"], r["county_fips"]) for r in FIXTURE]
            if self.fault == "missing":
                self.rows.pop()
            if self.fault == "duplicate":
                self.rows[-1] = self.rows[0]
            if self.fault == "excess":
                self.rows.append(self.rows[0])

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


@pytest.mark.parametrize(
    "fault", [None, "identity", "release", "denied", "missing", "duplicate", "excess"]
)
def test_service_probe_requires_primary_identity_release_and_ten_decoded_pairs(monkeypatch, fault):
    session = Session(fault)
    monkeypatch.setattr(probe, "connect", lambda _: session)
    result = probe.probe_county_context(settings())
    assert result["publication_matches"] == (fault is None)
    assert len(session.calls) <= 3
    assert "fixture-secret" not in json.dumps(result)
    if fault == "identity":
        assert len(session.calls) == 1 and result["identity_status"] == "mismatch"
    elif fault == "denied":
        assert result["read_status"] == "object_or_access_unavailable"
    elif fault is None:
        assert result["decoded_observations"] == 10
        sql, parameters = session.calls[-1]
        assert "context_contract_version=%s" in sql and "LIMIT 11" in sql
        assert len(parameters) == 7
