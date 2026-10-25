"""API-owned Snowflake connection parameters, key caching and import boundary."""

import ast
import base64
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from lyme_gap_atlas_api import snowflake_connection as module
from lyme_gap_atlas_api.config import ApiSettings

SRC = Path(module.__file__).parent
FORBIDDEN = ("lyme_gap_atlas_shared.snowflake", "lyme_gap_atlas_shared.infrastructure.snowflake")


def _pem(passphrase: bytes | None = None) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )


def _settings(**overrides):
    values = dict(
        snowflake_account="acct",
        snowflake_user="svc",
        snowflake_warehouse="wh",
        snowflake_role="role",
        snowflake_database="db",
        snowflake_network_timeout_seconds=30,
        snowflake_auth_method="key_pair",
    )
    values.update(overrides)
    return ApiSettings(_env_file=None, **values)


@pytest.fixture(autouse=True)
def _clean_cache():
    module.clear_private_key_cache()
    yield
    module.clear_private_key_cache()


def _base(**extra):
    return {
        "account": "acct",
        "user": "svc",
        "warehouse": "wh",
        "role": "role",
        "login_timeout": 15,
        "network_timeout": 30,
        "autocommit": True,
        "session_parameters": {"QUERY_TAG": "one-health-lyme-gap-atlas"},
        **extra,
    }


def test_api_src_never_imports_shared_snowflake_connection_modules():
    offenders = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any(n == f or n.startswith(f + ".") for n in names for f in FORBIDDEN):
                offenders.append(path.name)
    assert offenders == []


def test_pat_parameters_with_and_without_database():
    settings = _settings(snowflake_auth_method="pat", snowflake_pat=SecretStr("pat-value"))
    assert module.connection_parameters(settings) == _base(database="db", password="pat-value")
    assert module.connection_parameters(settings, include_database=False) == _base(
        password="pat-value"
    )


def test_pat_requires_value():
    with pytest.raises(ValueError, match="SNOWFLAKE_PAT"):
        module.connection_parameters(_settings(snowflake_auth_method="pat"))


def test_key_pair_parameters_from_b64_and_path(tmp_path):
    pem = _pem(b"phrase")
    expected_public = (
        serialization.load_pem_private_key(pem, b"phrase").public_key().public_numbers()
    )
    path = tmp_path / "key.pem"
    path.write_bytes(pem)
    for source in (
        {"snowflake_private_key_b64": SecretStr(base64.b64encode(pem).decode())},
        {"snowflake_private_key_path": path},
    ):
        parameters = module.connection_parameters(
            _settings(snowflake_private_key_passphrase=SecretStr("phrase"), **source)
        )
        key = parameters.pop("private_key")
        assert key.public_key().public_numbers() == expected_public
        assert parameters == _base(database="db", authenticator="SNOWFLAKE_JWT")


def test_key_pair_requires_key_material():
    with pytest.raises(ValueError, match="PRIVATE_KEY"):
        module.connection_parameters(_settings())


def test_monitor_parameters_and_connect_routing(monkeypatch):
    settings = _settings(snowflake_auth_method="pat", snowflake_pat=SecretStr("p"))
    parameters = module.monitor_connection_parameters(
        settings, include_database=False, timeout_seconds=10
    )
    backoff = parameters.pop("backoff_policy")
    assert list(backoff()) == [0]
    assert parameters == _base(
        password="p",
        login_timeout=10,
        network_timeout=3,
        socket_timeout=3,
        client_session_keep_alive=False,
        client_telemetry_enabled=False,
    )
    short = module.monitor_connection_parameters(settings, timeout_seconds=1.5)
    assert (short["network_timeout"], short["socket_timeout"]) == (1.5, 1.5)

    connector = Mock(return_value="conn")
    monkeypatch.setattr(module.snowflake.connector, "connect", connector)
    assert module.connect(settings) == "conn"
    assert connector.call_args.kwargs == _base(database="db", password="p")
    module.connect(settings, timeout_seconds=2)
    assert connector.call_args.kwargs["login_timeout"] == 2
    assert "backoff_policy" in connector.call_args.kwargs


def test_private_key_is_parsed_once_and_reloaded_on_rotation(monkeypatch):
    parse = Mock(wraps=serialization.load_pem_private_key)
    monkeypatch.setattr(module.serialization, "load_pem_private_key", parse)
    first, second = _pem(), _pem()

    def settings_for(pem):
        return _settings(snowflake_private_key_b64=SecretStr(base64.b64encode(pem).decode()))

    one = module.connection_parameters(settings_for(first))["private_key"]
    assert module.connection_parameters(settings_for(first))["private_key"] is one
    assert parse.call_count == 1
    rotated = module.connection_parameters(settings_for(second))["private_key"]
    assert rotated is not one and parse.call_count == 2
    # Same key bytes with a different passphrase must not hit the cache entry.
    assert module._key_digest(first, None) != module._key_digest(first, b"x")


def test_failed_parse_is_not_cached_and_cache_is_bounded(monkeypatch):
    with pytest.raises(ValueError):
        module._load_private_key(b"not a key", None)
    assert len(module._KEY_CACHE) == 0
    for _ in range(module._KEY_CACHE_LIMIT + 3):
        module._load_private_key(_pem(), None)
    assert len(module._KEY_CACHE) == module._KEY_CACHE_LIMIT
    assert all(isinstance(k, bytes) and len(k) == 32 for k in module._KEY_CACHE)
