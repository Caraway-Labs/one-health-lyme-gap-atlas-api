"""Server-only Supabase profile persistence adapter."""

import logging
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

import httpx
from pydantic import ValidationError

from .config import ApiSettings
from .models import UserProfile, UserProfileWrite

logger = logging.getLogger(__name__)


class ProfileStoreError(RuntimeError):
    """Safe classification of a profile persistence failure for operations logs."""

    def __init__(self, operation: str, category: str, upstream_status: int | None = None) -> None:
        super().__init__("Supabase profile service is unavailable")
        self.operation = operation
        self.category = category
        self.upstream_status = upstream_status


class ProfileStore(Protocol):
    def get(self, user_id: UUID) -> UserProfile | None: ...

    def save(self, user_id: UUID, profile: UserProfileWrite) -> UserProfile: ...


class SupabaseProfileStore:
    """Use the server-only secret key; no user ID is accepted from the client."""

    def __init__(self, settings: ApiSettings, client: httpx.Client | None = None) -> None:
        secret = settings.supabase_secret_key
        if not settings.supabase_url or secret is None:
            raise ValueError("Supabase profile configuration is incomplete")
        self._url = f"{settings.supabase_url.rstrip('/')}/rest/v1/user_profiles"
        self._secret = secret.get_secret_value()
        self._client = client or httpx.Client(timeout=10.0)

    def get(self, user_id: UUID) -> UserProfile | None:
        response = self._request(
            "GET",
            params={"user_id": f"eq.{user_id}", "select": "role,state_code,organization,job_title"},
        )
        payload = self._json_list(response, operation="get")
        if not payload:
            return None
        return self._profile_from_response(payload[0], operation="get")

    def save(self, user_id: UUID, profile: UserProfileWrite) -> UserProfile:
        payload = {"user_id": str(user_id), **profile.model_dump()}
        response = self._request(
            "POST",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            params={"select": "role,state_code,organization,job_title"},
            json=payload,
        )
        result = self._json_list(response, operation="save")
        if len(result) != 1:
            raise ProfileStoreError(operation="save", category="unexpected_response_count")
        return self._profile_from_response(result[0], operation="save")

    def _request(self, method: str, **kwargs: Any) -> httpx.Response:
        headers = {
            "apikey": self._secret,
            "Authorization": f"Bearer {self._secret}",
            "Accept-Profile": "atlas_accounts",
            "Content-Profile": "atlas_accounts",
        }
        headers.update(kwargs.pop("headers", {}))
        operation = method.lower()
        try:
            response = self._client.request(method, self._url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            upstream_status = (
                exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            )
            logger.warning(
                "profile_store_request_failed",
                extra={
                    "context": {
                        "operation": operation,
                        "failure_category": "upstream_http",
                        "upstream_status": upstream_status,
                    }
                },
            )
            raise ProfileStoreError(
                operation=operation,
                category="upstream_http",
                upstream_status=upstream_status,
            ) from exc

    @staticmethod
    def _json_list(response: httpx.Response, operation: str) -> list[Mapping[str, Any]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProfileStoreError(operation=operation, category="invalid_json") from exc
        if not isinstance(payload, list) or not all(isinstance(row, Mapping) for row in payload):
            raise ProfileStoreError(operation=operation, category="invalid_response_shape")
        return payload

    @staticmethod
    def _profile_from_response(row: Mapping[str, Any], operation: str) -> UserProfile:
        try:
            return UserProfile.model_validate(row)
        except ValidationError as exc:
            raise ProfileStoreError(
                operation=operation, category="invalid_profile_response"
            ) from exc
