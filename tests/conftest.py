"""Shared pytest hooks for the API test suite."""

from __future__ import annotations

import os
import shutil
from typing import Any

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
def _fresh_write_session_pool(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolate the process-wide write pool so fake connections never leak across tests."""
    from lyme_gap_atlas_api import snowflake_write_session

    pool = snowflake_write_session.WriteSessionPool()
    monkeypatch.setattr(snowflake_write_session, "_POOL", pool)
    yield pool
    pool.close_all()
