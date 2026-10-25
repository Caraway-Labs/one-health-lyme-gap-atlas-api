"""API-owned Snowflake connection construction (replaces the deprecated shared module).

Parameters are behavior-identical to ``lyme_gap_atlas_shared.infrastructure.snowflake``
except for an explicit ``autocommit=True``. Settings stay owned by shared-python.
"""

import base64
import hashlib
import hmac
import os
import threading
from collections import OrderedDict
from typing import Any

import snowflake.connector
from cryptography.hazmat.primitives import serialization
from lyme_gap_atlas_shared.settings import SnowflakeSettings
from snowflake.connector import SnowflakeConnection

QUERY_TAG = "one-health-lyme-gap-atlas"
LOGIN_TIMEOUT_SECONDS = 15
_KEY_CACHE_LIMIT = 4

# Per-process HMAC key: cache identifiers never leave memory and cannot be matched
# against key material elsewhere. Only the parsed key object is retained.
_DIGEST_KEY = os.urandom(32)
_KEY_CACHE: OrderedDict[bytes, Any] = OrderedDict()
_KEY_LOCK = threading.Lock()


def _key_digest(key_bytes: bytes, passphrase: bytes | None) -> bytes:
    mac = hmac.new(_DIGEST_KEY, digestmod=hashlib.sha256)
    mac.update(len(key_bytes).to_bytes(8, "big"))
    mac.update(key_bytes)
    mac.update(b"\x00" if passphrase is None else b"\x01" + passphrase)
    return mac.digest()


def _load_private_key(key_bytes: bytes, passphrase: bytes | None) -> Any:
    """Parse once per distinct key+passphrase; a rotated key or passphrase reloads.

    Failed parses raise before anything is stored, so they are never cached.
    """
    digest = _key_digest(key_bytes, passphrase)
    with _KEY_LOCK:
        cached = _KEY_CACHE.get(digest)
        if cached is not None:
            _KEY_CACHE.move_to_end(digest)
            return cached
    key = serialization.load_pem_private_key(key_bytes, password=passphrase)
    with _KEY_LOCK:
        _KEY_CACHE[digest] = key
        _KEY_CACHE.move_to_end(digest)
        while len(_KEY_CACHE) > _KEY_CACHE_LIMIT:
            _KEY_CACHE.popitem(last=False)
    return key


def clear_private_key_cache() -> None:
    with _KEY_LOCK:
        _KEY_CACHE.clear()


def connection_parameters(
    settings: SnowflakeSettings, *, include_database: bool = True
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "account": settings.snowflake_account,
        "user": settings.snowflake_user,
        "warehouse": settings.snowflake_warehouse,
        "role": settings.snowflake_role,
        "login_timeout": LOGIN_TIMEOUT_SECONDS,
        "network_timeout": settings.snowflake_network_timeout_seconds,
        # Stored procedures run in autocommit; making the session AUTOCOMMIT explicit
        # keeps it deterministic and removes the need for commit/rollback round trips.
        "autocommit": True,
        "session_parameters": {"QUERY_TAG": QUERY_TAG},
    }
    if include_database:
        parameters["database"] = settings.snowflake_database
    if settings.snowflake_auth_method == "pat":
        if settings.snowflake_pat is None:
            raise ValueError("SNOWFLAKE_PAT is required for PAT authentication")
        # Snowflake's Python connector accepts a PAT in the password field and
        # detects it during standard authentication. Setting the endpoint PAT
        # authenticator instead would make the connector read ``token`` and use
        # the REST endpoint flow rather than the documented connector flow.
        parameters["password"] = settings.snowflake_pat.get_secret_value()
        return parameters

    if settings.snowflake_private_key_b64 is None and settings.snowflake_private_key_path is None:
        raise ValueError(
            "SNOWFLAKE_PRIVATE_KEY_B64 or SNOWFLAKE_PRIVATE_KEY_PATH is required "
            "for key-pair authentication"
        )
    passphrase = (
        settings.snowflake_private_key_passphrase.get_secret_value().encode()
        if settings.snowflake_private_key_passphrase
        else None
    )
    if settings.snowflake_private_key_b64 is not None:
        private_key_bytes = base64.b64decode(settings.snowflake_private_key_b64.get_secret_value())
    else:
        assert settings.snowflake_private_key_path is not None
        private_key_bytes = settings.snowflake_private_key_path.read_bytes()
    parameters["private_key"] = _load_private_key(private_key_bytes, passphrase)
    parameters["authenticator"] = "SNOWFLAKE_JWT"
    return parameters


def monitor_connection_parameters(
    settings: SnowflakeSettings, *, include_database: bool = True, timeout_seconds: float
) -> dict[str, Any]:
    parameters = connection_parameters(settings, include_database=include_database)
    parameters.update(
        login_timeout=timeout_seconds,
        network_timeout=min(timeout_seconds, 3),
        socket_timeout=min(timeout_seconds, 3),
        # Bound SDK retries for the monitor only; product policy is unchanged.
        backoff_policy=lambda: iter([0]),
        client_session_keep_alive=False,
        client_telemetry_enabled=False,
    )
    return parameters


def connect(
    settings: SnowflakeSettings,
    *,
    include_database: bool = True,
    timeout_seconds: float | None = None,
) -> SnowflakeConnection:
    """Connect to Snowflake, optionally before the target database exists."""
    if timeout_seconds is None:
        parameters = connection_parameters(settings, include_database=include_database)
    else:
        parameters = monitor_connection_parameters(
            settings, include_database=include_database, timeout_seconds=timeout_seconds
        )
    return snowflake.connector.connect(**parameters)
