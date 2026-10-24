"""The one definition of the Snowflake session baseline for pooled read connections.

The query tag is part of the baseline too, but it is set at connect time
(``snowflake_connection.QUERY_TAG``). Callers that need a different session state must
go through ``ReadSessionPool.session_overrides`` so the pool can restore or discard it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .repository import AtlasDataUnavailableError

if TYPE_CHECKING:
    from snowflake.connector import SnowflakeConnection

    from .config import ApiSettings

MAX_STATEMENT_TIMEOUT_SECONDS = 30
SECONDARY_ROLES_SQL = "USE SECONDARY ROLES NONE"
IDENTITY_SQL = "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_DATABASE(), CURRENT_WAREHOUSE()"


def baseline_timeout_seconds(settings: ApiSettings) -> int:
    return min(settings.public_query_timeout_seconds, MAX_STATEMENT_TIMEOUT_SECONDS)


def timeout_statement(seconds: int) -> str:
    return f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS={int(seconds)}"


def baseline_statements(settings: ApiSettings) -> tuple[str, ...]:
    return (timeout_statement(baseline_timeout_seconds(settings)), SECONDARY_ROLES_SQL)


def identity_matches(settings: ApiSettings, row: Any) -> bool:
    """Compare the session identity with the configured one, case-insensitively.

    A setting that is not configured (empty) has nothing to assert against.
    """
    try:
        actual = tuple(row)
    except TypeError:
        return False
    expected = (
        settings.snowflake_user,
        settings.snowflake_role,
        settings.snowflake_database,
        settings.snowflake_warehouse,
    )
    if len(actual) != len(expected):
        return False
    return all(
        not want or (isinstance(got, str) and got.strip().upper() == want.strip().upper())
        for want, got in zip(expected, actual, strict=True)
    )


def apply_baseline(connection: SnowflakeConnection, settings: ApiSettings) -> None:
    """Apply the baseline and verify the identity on a new pooled connection.

    One cursor, three statements. Raises ``AtlasDataUnavailableError`` (without leaking
    identity values) if setup fails or the session is not the configured service identity;
    the caller closes the connection.
    """
    timeout = baseline_timeout_seconds(settings)
    try:
        with connection.cursor() as cursor:
            for statement in baseline_statements(settings):
                cursor.execute(statement, timeout=timeout)
            cursor.execute(IDENTITY_SQL, timeout=timeout)
            row = cursor.fetchone()
    except Exception:
        raise AtlasDataUnavailableError("Snowflake session setup failed") from None
    if not identity_matches(settings, row):
        raise AtlasDataUnavailableError("Snowflake session identity does not match configuration")
