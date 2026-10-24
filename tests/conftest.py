"""Shared pytest hooks for the API test suite."""

from __future__ import annotations

import os
import shutil

import pytest

# pytest-cov exports these so every child Python starts coverage. The report
# renderer tests launch a Python stand-in for Typst; tracing that process walks
# large third-party modules and exceeds the renderer timeout. Measurement of
# this process is unchanged.
_COV_SUBPROCESS_ENV = (
    "COV_CORE_SOURCE",
    "COV_CORE_CONFIG",
    "COV_CORE_DATAFILE",
    "COV_CORE_BRANCH",
    "COV_CORE_CONTEXT",
)


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    if session.config.pluginmanager.getplugin("_cov") is None:
        return
    for name in _COV_SUBPROCESS_ENV:
        os.environ.pop(name, None)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-real-typst",
        action="store_true",
        default=False,
        help="Fail rather than skip real PDF tests when the production Typst binary is absent.",
    )


@pytest.fixture
def real_typst_binary(request: pytest.FixtureRequest) -> str:
    binary = shutil.which("typst")
    if binary is None:
        if request.config.getoption("--require-real-typst"):
            pytest.fail("Production Typst binary is required but unavailable on PATH.")
        pytest.skip("Production Typst binary unavailable")
    return binary


@pytest.fixture(autouse=True)
def _stub_session_baseline(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pool-creation baseline needs a cursor that answers the identity query.

    Most suites fake connections for other behavior, so the pool's baseline step is a
    no-op for them. Modules proving the baseline itself set ``REAL_SESSION_BASELINE = True``
    (test_snowflake_read_session, test_cdc_request_session).
    """
    if getattr(request.module, "REAL_SESSION_BASELINE", False):
        return
    monkeypatch.setattr("lyme_gap_atlas_api.snowflake_read_session.apply_baseline", lambda *_: None)
