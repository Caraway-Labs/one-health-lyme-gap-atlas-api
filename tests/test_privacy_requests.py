from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from test_api import FakeRepository, FakeTokenVerifier, MemoryProfileStore

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.auth_admin import AuthAdminError, AuthUserSnapshot
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.models import UserProfileWrite
from lyme_gap_atlas_api.privacy_requests import (
    MemoryPrivacyRequestStore,
    PrivacyRequestRecord,
    PrivacyRequestStore,
    PrivacyRequestStoreError,
    _redacted_error_class,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER = UUID("22222222-2222-2222-2222-222222222222")


class MemoryAuthAdmin:
    def __init__(self) -> None:
        self.deleted: list[UUID] = []
        self.fail_delete = False

    def get_user(self, user_id: UUID) -> AuthUserSnapshot:
        return AuthUserSnapshot(
            email="analyst@example.com",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
            last_sign_in_at=datetime(2026, 9, 11, tzinfo=UTC),
        )

    def delete_user(self, user_id: UUID) -> None:
        if self.fail_delete:
            raise AuthAdminError("delete", "upstream_http", 503)
        self.deleted.append(user_id)


def _api(
    *,
    token_verifier: FakeTokenVerifier | None = None,
    auth_admin: MemoryAuthAdmin | None = None,
    store: PrivacyRequestStore | None = None,
    profiles: MemoryProfileStore | None = None,
) -> tuple[TestClient, MemoryProfileStore, MemoryAuthAdmin]:
    profile_store = profiles or MemoryProfileStore()
    admin = auth_admin or MemoryAuthAdmin()
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
            token_verifier=token_verifier or FakeTokenVerifier(USER_ID),
            privacy_request_store=store or MemoryPrivacyRequestStore(),
            auth_admin=admin,
        )
    )
    return api, profile_store, admin


def test_privacy_requests_require_authentication() -> None:
    api, _, _ = _api()
    assert api.post("/v1/me/privacy-requests", json={"action": "export"}).status_code == 401


def test_export_returns_versioned_envelope_with_omissions() -> None:
    api, profiles, _ = _api()
    headers = {"Authorization": "Bearer test-token"}
    profiles.save(
        USER_ID,
        UserProfileWrite(
            role="state_level_epidemiologist",
            state_code="CO",
            organization="Caraway Labs",
            job_title="Analyst",
        ),
    )
    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "export"})
    assert created.status_code == 200
    assert created.headers["cache-control"] == "private, no-store"
    request_id = created.json()["request_id"]
    nonce = created.json()["confirmation_nonce"]
    confirmed = api.post(
        f"/v1/me/privacy-requests/{request_id}/confirm",
        headers=headers,
        json={"nonce": nonce},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "completed"
    assert confirmed.json()["download_available"] is True
    download = api.get(f"/v1/me/privacy-requests/{request_id}/export", headers=headers)
    assert download.status_code == 200
    payload = download.json()
    assert payload["schema_version"] == "atlas-user-data-export/v1"
    assert payload["data"]["account"]["email"] == "analyst@example.com"
    assert payload["data"]["profile"]["state_code"] == "CO"
    assert {item["reason"] for item in payload["omissions"]} == {
        "not_connected",
        "not_account_linked",
        "browser_only",
    }
    assert "token" not in str(payload).lower()
    assert api.get("/v1/atlas/scores").status_code == 200


def test_cross_user_privacy_request_is_not_found() -> None:
    store = MemoryPrivacyRequestStore()
    owner_api, _, _ = _api(store=store)
    headers = {"Authorization": "Bearer test-token"}
    created = owner_api.post("/v1/me/privacy-requests", headers=headers, json={"action": "export"})
    request_id = created.json()["request_id"]
    nonce = created.json()["confirmation_nonce"]
    other, _, _ = _api(token_verifier=FakeTokenVerifier(OTHER_USER), store=store)
    assert other.get(f"/v1/me/privacy-requests/{request_id}", headers=headers).status_code == 404
    assert (
        other.post(
            f"/v1/me/privacy-requests/{request_id}/confirm",
            headers=headers,
            json={"nonce": nonce},
        ).status_code
        == 404
    )


def test_expired_nonce_and_replay_are_rejected() -> None:
    api, _, _ = _api()
    headers = {"Authorization": "Bearer test-token"}
    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "export"})
    request_id = created.json()["request_id"]
    nonce = created.json()["confirmation_nonce"]
    first = api.post(
        f"/v1/me/privacy-requests/{request_id}/confirm",
        headers=headers,
        json={"nonce": nonce},
    )
    assert first.status_code == 200
    replay = api.post(
        f"/v1/me/privacy-requests/{request_id}/confirm",
        headers=headers,
        json={"nonce": nonce},
    )
    assert replay.status_code == 409
    unknown = api.post(
        f"/v1/me/privacy-requests/{uuid4()}/confirm",
        headers=headers,
        json={"nonce": "a" * 24},
    )
    assert unknown.status_code == 404


def test_stale_session_cannot_confirm() -> None:
    stale = FakeTokenVerifier(USER_ID, issued_at=datetime.now(UTC) - timedelta(minutes=20))
    api, _, _ = _api(token_verifier=stale)
    headers = {"Authorization": "Bearer test-token"}
    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "export"})
    confirmed = api.post(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": created.json()["confirmation_nonce"]},
    )
    assert confirmed.status_code == 401
    assert "recently signed-in" in confirmed.json()["detail"]


def test_deletion_removes_auth_user_and_leaves_public_atlas() -> None:
    api, _, admin = _api()
    headers = {"Authorization": "Bearer test-token"}
    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "deletion"})
    confirmed = api.post(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": created.json()["confirmation_nonce"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "completed"
    assert admin.deleted == [USER_ID]
    assert api.get("/v1/atlas/scores").status_code == 200
    export = api.get(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/export",
        headers=headers,
    )
    assert export.status_code == 409


class _FailAfterClaimStore(MemoryPrivacyRequestStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_save = False

    def save(self, record: PrivacyRequestRecord) -> PrivacyRequestRecord:
        if self.fail_next_save:
            self.fail_next_save = False
            raise PrivacyRequestStoreError("save", "invalid_json")
        return super().save(record)


def test_deletion_stays_completed_when_ledger_save_fails_after_auth_delete() -> None:
    store = _FailAfterClaimStore()
    api, _, admin = _api(store=store)
    headers = {"Authorization": "Bearer test-token"}
    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "deletion"})
    store.fail_next_save = True
    confirmed = api.post(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": created.json()["confirmation_nonce"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "completed"
    assert admin.deleted == [USER_ID]


def test_deletion_scrubs_prior_export_payloads() -> None:
    store = MemoryPrivacyRequestStore()
    api, _, admin = _api(store=store)
    headers = {"Authorization": "Bearer test-token"}
    export = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "export"})
    api.post(
        f"/v1/me/privacy-requests/{export.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": export.json()["confirmation_nonce"]},
    )
    export_row = store.get(UUID(export.json()["request_id"]))
    assert export_row is not None
    assert export_row.export_payload is not None

    deletion = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "deletion"})
    confirmed = api.post(
        f"/v1/me/privacy-requests/{deletion.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": deletion.json()["confirmation_nonce"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "completed"
    assert admin.deleted == [USER_ID]
    assert all(row.export_payload is None for row in store.records.values())
    assert all(row.export_expires_at is None for row in store.records.values())


def test_processor_failures_use_allowed_error_classes() -> None:
    assert (
        _redacted_error_class(PrivacyRequestStoreError("save", "invalid_json"))
        == "auth_admin_unavailable"
    )
    assert _redacted_error_class(AuthAdminError("delete", "upstream_http", 503)) == "upstream_http"
    assert _redacted_error_class(RuntimeError("boom")) == "processor_failure"

    admin = MemoryAuthAdmin()
    admin.fail_delete = True
    api, _, _ = _api(auth_admin=admin)
    headers = {"Authorization": "Bearer test-token"}
    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "deletion"})
    confirmed = api.post(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": created.json()["confirmation_nonce"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "needs_support"
