"""Connectivity diagnostics classify failures without leaking driver messages."""

import logging
import socket
import ssl
from typing import Any

import pytest
from neo4j.exceptions import (
    AuthError,
    ConfigurationError,
    ConnectionAcquisitionTimeoutError,
    ServiceUnavailable,
    SessionExpired,
)

from lyme_gap_atlas_api.knowledge_chat import Neo4jRetriever, _neo4j_failure_category

SECRET = "credential-hostname-private-content"


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (AuthError(SECRET), "authentication"),
        (ConfigurationError(SECRET), "configuration"),
        (ssl.SSLError(SECRET), "tls"),
        (socket.gaierror(SECRET), "dns"),
        (TimeoutError(SECRET), "timeout"),
        (ConnectionAcquisitionTimeoutError(SECRET), "timeout"),
        (ConnectionRefusedError(SECRET), "connection_refused"),
        (ServiceUnavailable(SECRET), "service_unavailable"),
        (SessionExpired(SECRET), "session_expired"),
        (RuntimeError(SECRET), "unknown"),
    ],
)
def test_readiness_categories_remain_private(error: Exception, category: str, caplog: Any) -> None:
    class Driver:
        def verify_connectivity(self) -> None:
            raise error

    retriever = object.__new__(Neo4jRetriever)
    retriever._driver = Driver()  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING):
        assert retriever.ready() is False
    record = caplog.records[-1]
    assert record.context == {"request_id": "unavailable", "error_category": category}
    assert not record.exc_info
    assert SECRET not in caplog.text
    assert SECRET not in str(record.context)


def test_wrapped_timeout_is_more_specific_than_service_unavailable() -> None:
    error = ServiceUnavailable(SECRET)
    error.__cause__ = TimeoutError(SECRET)
    assert _neo4j_failure_category(error) == "timeout"


def test_cyclic_exception_chain_is_bounded() -> None:
    error = RuntimeError(SECRET)
    error.__cause__ = error
    assert _neo4j_failure_category(error) == "unknown"


@pytest.mark.parametrize(
    "inner",
    [
        socket.gaierror(SECRET),
        ssl.SSLError(SECRET),
        ConnectionRefusedError(SECRET),
        TimeoutError(SECRET),
    ],
)
def test_driver_routing_group_retains_type_only_category(inner: Exception, caplog: Any) -> None:
    error = ServiceUnavailable(SECRET)
    error.__cause__ = ExceptionGroup(SECRET, [inner])
    expected = _neo4j_failure_category(inner)
    assert _neo4j_failure_category(error) == expected
    assert SECRET not in caplog.text


def test_driver_suppressed_tcp_timeout_context_is_inspected() -> None:
    try:
        try:
            raise TimeoutError(SECRET)
        except TimeoutError:
            raise ServiceUnavailable(SECRET) from None
    except ServiceUnavailable as error:
        assert error.__suppress_context__
        assert _neo4j_failure_category(error) == "timeout"


def test_group_traversal_has_eight_exception_bound() -> None:
    error = ExceptionGroup(SECRET, [RuntimeError(SECRET) for _ in range(8)] + [AuthError(SECRET)])
    assert _neo4j_failure_category(error) == "unknown"


def test_success_adds_no_failure_log(caplog: Any) -> None:
    class Driver:
        def verify_connectivity(self) -> None:
            pass

    retriever = object.__new__(Neo4jRetriever)
    retriever._driver = Driver()  # type: ignore[assignment]
    assert retriever.ready() is True
    assert not caplog.records
