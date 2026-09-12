"""GoTrue admin adapter for account export metadata and user deletion."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import httpx

from .config import ApiSettings

logger = logging.getLogger(__name__)


class AuthAdminError(RuntimeError):
    def __init__(self, operation: str, category: str, upstream_status: int | None = None) -> None:
        super().__init__("Supabase auth admin is unavailable")
        self.operation = operation
        self.category = category
        self.upstream_status = upstream_status


@dataclass(frozen=True)
class AuthUserSnapshot:
    email: str | None
    created_at: datetime | None
    last_sign_in_at: datetime | None


class AuthAdmin(Protocol):
    def get_user(self, user_id: UUID) -> AuthUserSnapshot: ...

    def delete_user(self, user_id: UUID) -> None: ...


class SupabaseAuthAdmin:
    def __init__(self, settings: ApiSettings, client: httpx.Client | None = None) -> None:
        secret = settings.supabase_secret_key
        if not settings.supabase_url or secret is None:
            raise ValueError("Supabase auth admin configuration is incomplete")
        self._base = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users"
        self._secret = secret.get_secret_value()
        self._client = client or httpx.Client(timeout=10.0)

    def get_user(self, user_id: UUID) -> AuthUserSnapshot:
        payload = self._json(self._request("GET", f"{self._base}/{user_id}"), operation="get_user")
        return AuthUserSnapshot(
            email=_optional_str(payload.get("email")),
            created_at=_optional_datetime(payload.get("created_at")),
            last_sign_in_at=_optional_datetime(payload.get("last_sign_in_at")),
        )

    def delete_user(self, user_id: UUID) -> None:
        self._request("DELETE", f"{self._base}/{user_id}")

    def _request(self, method: str, url: str) -> httpx.Response:
        try:
            response = self._client.request(
                method,
                url,
                headers={
                    "apikey": self._secret,
                    "Authorization": f"Bearer {self._secret}",
                },
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            upstream_status = (
                exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            )
            logger.warning(
                "auth_admin_request_failed",
                extra={
                    "context": {
                        "operation": method.lower(),
                        "failure_category": "upstream_http",
                        "upstream_status": upstream_status,
                    }
                },
            )
            raise AuthAdminError(
                operation=method.lower(),
                category="upstream_http",
                upstream_status=upstream_status,
            ) from exc

    @staticmethod
    def _json(response: httpx.Response, operation: str) -> dict[str, object]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthAdminError(operation=operation, category="invalid_json") from exc
        if not isinstance(payload, dict):
            raise AuthAdminError(operation=operation, category="invalid_response_shape")
        return payload


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
