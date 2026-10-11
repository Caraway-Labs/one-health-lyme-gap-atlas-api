"""Starting-view onboarding marker on the private profile contract."""

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import FakeRepository, FakeTokenVerifier, MemoryProfileStore

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.models import UserProfile, UserProfileWrite
from lyme_gap_atlas_api.profiles import (
    PROFILE_COLUMNS,
    ProfileStoreError,
    SupabaseProfileStore,
    profile_upsert_body,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20261011161500_add_starting_view_version.sql"


def _api(store: MemoryProfileStore | None = None) -> tuple[TestClient, MemoryProfileStore]:
    profile_store = store or MemoryProfileStore()
    api = TestClient(
        create_app(
            FakeRepository(),
            ApiSettings(
                snowflake_account="test",
                snowflake_user="test",
                snowflake_role="test",
                snowflake_pat="test",
            ),
            profile_store=profile_store,
            token_verifier=FakeTokenVerifier(USER_ID),
        )
    )
    return api, profile_store


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _store(handler: httpx.MockTransport) -> SupabaseProfileStore:
    return SupabaseProfileStore(
        ApiSettings(
            snowflake_account="test",
            snowflake_user="test",
            snowflake_role="test",
            snowflake_pat="test",
            supabase_url="https://example.supabase.co",
            supabase_secret_key="server-secret",
        ),
        client=httpx.Client(transport=handler),
    )


def test_migration_adds_nullable_marker_without_backfill() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "add column starting_view_version integer" in sql
    assert "starting_view_version = 1" in sql
    assert "update atlas_accounts.user_profiles" not in sql
    assert "not null" not in sql


def test_legacy_profile_without_marker_stays_incomplete() -> None:
    profile = UserProfile.model_validate(
        {
            "role": "state_level_epidemiologist",
            "state_code": None,
            "organization": "Example organization",
            "job_title": None,
        }
    )

    assert profile.starting_view_version is None
    assert profile.model_dump()["starting_view_version"] is None


def test_ordinary_save_of_legacy_profile_does_not_complete_onboarding() -> None:
    api, _store = _api()
    saved = api.put(
        "/v1/me/profile",
        headers=_headers(),
        json={"role": "general_public_citizen", "state_code": None, "job_title": "Analyst"},
    )

    assert saved.status_code == 200
    assert saved.json()["profile"]["starting_view_version"] is None
    assert saved.json()["profile"]["state_code"] is None
    assert "complete_starting_view" not in saved.json()["profile"]
    loaded = api.get("/v1/me/profile", headers=_headers())
    assert loaded.json()["profile"]["starting_view_version"] is None


def test_national_completion_saves_null_state_and_version_one() -> None:
    api, _store = _api()
    saved = api.put(
        "/v1/me/profile",
        headers=_headers(),
        json={
            "role": None,
            "state_code": None,
            "organization": None,
            "job_title": None,
            "complete_starting_view": True,
        },
    )

    assert saved.status_code == 200
    profile = saved.json()["profile"]
    assert profile["state_code"] is None
    assert profile["starting_view_version"] == 1
    assert profile["role"] is None
    loaded = api.get("/v1/me/profile", headers=_headers())
    assert loaded.json()["profile"] == profile


def test_state_completion_normalizes_and_returns_version_one() -> None:
    api, _store = _api()
    saved = api.put(
        "/v1/me/profile",
        headers=_headers(),
        json={
            "state_code": "co",
            "organization": "Caraway Labs",
            "complete_starting_view": True,
        },
    )

    assert saved.status_code == 200
    assert saved.json()["profile"]["state_code"] == "CO"
    assert saved.json()["profile"]["organization"] == "Caraway Labs"
    assert saved.json()["profile"]["starting_view_version"] == 1


def test_invalid_jurisdiction_does_not_complete_or_persist() -> None:
    api, store = _api()
    headers = _headers()
    for code in ("ZZ", "PR", "pr"):
        rejected = api.put(
            "/v1/me/profile",
            headers=headers,
            json={"state_code": code, "complete_starting_view": True},
        )
        assert rejected.status_code == 422
    assert store.profiles == {}
    assert api.get("/v1/me/profile", headers=headers).json()["profile"] is None


def test_client_cannot_set_or_clear_the_version() -> None:
    api, store = _api()
    headers = _headers()
    for body in (
        {"state_code": "CO", "starting_view_version": 1},
        {"state_code": "CO", "starting_view_version": 2},
        {"state_code": "CO", "complete_starting_view": False},
    ):
        rejected = api.put("/v1/me/profile", headers=headers, json=body)
        assert rejected.status_code == 422
    assert store.profiles == {}


def test_ordinary_settings_save_preserves_completion_marker() -> None:
    api, _store = _api()
    headers = _headers()
    completed = api.put(
        "/v1/me/profile",
        headers=headers,
        json={"state_code": "NY", "job_title": "Analyst", "complete_starting_view": True},
    )
    assert completed.status_code == 200

    ordinary = api.put(
        "/v1/me/profile",
        headers=headers,
        json={"state_code": "NY", "job_title": "Epidemiologist", "organization": "State lab"},
    )
    assert ordinary.status_code == 200
    assert ordinary.json()["profile"]["starting_view_version"] == 1
    assert ordinary.json()["profile"]["state_code"] == "NY"
    assert ordinary.json()["profile"]["job_title"] == "Epidemiologist"

    changed_default = api.put(
        "/v1/me/profile",
        headers=headers,
        json={"state_code": None, "job_title": "Epidemiologist", "organization": "State lab"},
    )
    assert changed_default.status_code == 200
    assert changed_default.json()["profile"]["state_code"] is None
    assert changed_default.json()["profile"]["starting_view_version"] == 1
    assert (
        api.get("/v1/me/profile", headers=headers).json()["profile"]["starting_view_version"] == 1
    )


def test_read_back_confirms_completed_jurisdiction_after_save() -> None:
    api, _store = _api()
    headers = _headers()
    saved = api.put(
        "/v1/me/profile",
        headers=headers,
        json={"state_code": "DC", "complete_starting_view": True},
    )
    assert saved.status_code == 200

    # A follow-up read is the confirmation signal after an uncertain save.
    # The marker is only the onboarding completion fact, not a write revision.
    loaded = api.get("/v1/me/profile", headers=headers)
    assert loaded.status_code == 200
    assert loaded.json()["profile"]["starting_view_version"] == 1
    assert loaded.json()["profile"]["state_code"] == "DC"


def test_completion_upsert_writes_jurisdiction_and_version_together() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["select"] = request.url.params["select"]
        seen["body"] = json.loads(request.content)
        body = seen["body"]
        assert isinstance(body, dict)
        return httpx.Response(
            201,
            json=[
                {
                    "role": body["role"],
                    "state_code": body["state_code"],
                    "organization": body["organization"],
                    "job_title": body["job_title"],
                    "starting_view_version": 1,
                }
            ],
        )

    saved = _store(httpx.MockTransport(handler)).save(
        USER_ID,
        UserProfileWrite(state_code="tx", job_title="Analyst", complete_starting_view=True),
    )

    assert seen["method"] == "POST"
    assert seen["select"] == PROFILE_COLUMNS
    assert seen["body"] == {
        "user_id": str(USER_ID),
        "role": None,
        "state_code": "TX",
        "organization": None,
        "job_title": "Analyst",
        "starting_view_version": 1,
    }
    assert saved.starting_view_version == 1
    assert saved.state_code == "TX"


def test_ordinary_upsert_omits_marker_and_read_returns_stored_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "starting_view_version" not in body
        assert "complete_starting_view" not in body
        return httpx.Response(
            200,
            json=[
                {
                    "role": None,
                    "state_code": "CO",
                    "organization": "State lab",
                    "job_title": None,
                    "starting_view_version": 1,
                }
            ],
        )

    saved = _store(httpx.MockTransport(handler)).save(
        USER_ID,
        UserProfileWrite(state_code="CO", organization="State lab"),
    )

    assert saved.starting_view_version == 1
    assert saved.organization == "State lab"
    ordinary = profile_upsert_body(
        USER_ID, UserProfileWrite(state_code=None, role="national_level_epidemiologist")
    )
    assert "starting_view_version" not in ordinary


def test_read_returns_legacy_null_marker() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["select"] == PROFILE_COLUMNS
        return httpx.Response(
            200,
            json=[
                {
                    "role": None,
                    "state_code": None,
                    "organization": None,
                    "job_title": None,
                    "starting_view_version": None,
                }
            ],
        )

    loaded = _store(httpx.MockTransport(handler)).get(USER_ID)
    assert loaded is not None
    assert loaded.starting_view_version is None
    assert loaded.state_code is None


def test_unrecognized_stored_marker_fails_closed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "role": None,
                    "state_code": "CO",
                    "organization": None,
                    "job_title": None,
                    "starting_view_version": 2,
                }
            ],
        )

    with pytest.raises(ProfileStoreError) as caught:
        _store(httpx.MockTransport(handler)).get(USER_ID)
    assert caught.value.category == "invalid_profile_response"
