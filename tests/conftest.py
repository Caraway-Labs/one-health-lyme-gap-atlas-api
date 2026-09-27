"""Shared pytest hooks for the API test suite."""

from __future__ import annotations

import os

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
