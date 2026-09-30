"""Opaque, tamper-evident pagination tokens for the single-process public API."""

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

_SECRET = secrets.token_bytes(32)


def encode(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.digest(_SECRET, body, hashlib.sha256)
    return base64.urlsafe_b64encode(body + signature).decode().rstrip("=")


def decode(token: str) -> dict[str, Any]:
    if not token or len(token) > 2048:
        raise ValueError("Invalid page_token.")
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        body, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(signature, hmac.digest(_SECRET, body, hashlib.sha256)):
            raise ValueError("Invalid page_token.")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Invalid page_token.")
        return payload
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ValueError("Invalid page_token.") from exc
