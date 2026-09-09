"""Server-only Supabase profile persistence adapter."""

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

import httpx

from .config import ApiSettings
from .models import UserProfile, UserProfileWrite


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
        payload = self._json_list(response)
        return UserProfile.model_validate(payload[0]) if payload else None

    def save(self, user_id: UUID, profile: UserProfileWrite) -> UserProfile:
        payload = {"user_id": str(user_id), **profile.model_dump()}
        response = self._request(
            "POST",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json=payload,
        )
        result = self._json_list(response)
        if len(result) != 1:
            raise RuntimeError("Supabase did not return a profile")
        return UserProfile.model_validate(result[0])

    def _request(self, method: str, **kwargs: Any) -> httpx.Response:
        headers = {
            "apikey": self._secret,
            "Authorization": f"Bearer {self._secret}",
            "Accept-Profile": "atlas_accounts",
            "Content-Profile": "atlas_accounts",
        }
        headers.update(kwargs.pop("headers", {}))
        try:
            response = self._client.request(method, self._url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            raise RuntimeError("Supabase profile service is unavailable") from exc

    @staticmethod
    def _json_list(response: httpx.Response) -> list[Mapping[str, Any]]:
        payload = response.json()
        if not isinstance(payload, list) or not all(isinstance(row, Mapping) for row in payload):
            raise RuntimeError("Supabase returned an invalid profile response")
        return payload
