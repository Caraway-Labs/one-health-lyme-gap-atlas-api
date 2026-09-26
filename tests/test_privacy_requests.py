from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from test_api import FakeRepository, FakeTokenVerifier, MemoryProfileStore

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.auth_admin import AuthAdminError, AuthUserSnapshot
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.feedback import MemoryFeedbackStore
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
    feedback_store: MemoryFeedbackStore | None = None,
) -> tuple[TestClient, MemoryProfileStore, MemoryAuthAdmin, MemoryFeedbackStore]:
    profile_store = profiles or MemoryProfileStore()
    admin = auth_admin or MemoryAuthAdmin()
    feedback = feedback_store or MemoryFeedbackStore()
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
            feedback_store=feedback,
        )
    )
    return api, profile_store, admin, feedback


def test_privacy_requests_require_authentication() -> None:
    api, _, _, _ = _api()
    assert api.post("/v1/me/privacy-requests", json={"action": "export"}).status_code == 401


def test_export_returns_versioned_envelope_with_omissions() -> None:
    api, profiles, _, _ = _api()
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
    systems = {item["system"] for item in confirmed.json()["omissions"]}
    assert "atlas-feedback-service" not in systems
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
    assert all(item["system"] != "atlas-feedback-service" for item in payload["omissions"])
    assert "token" not in str(payload).lower()
    assert api.get("/v1/atlas/scores").status_code == 200


def test_export_includes_only_that_account_feedback() -> None:
    feedback = MemoryFeedbackStore()
    api, _, _, _ = _api(feedback_store=feedback)
    headers = {"Authorization": "Bearer test-token"}
    own = feedback.submit(
        submission_token=str(uuid4()),
        payload_fingerprint="a" * 64,
        category="bug",
        message="Own account feedback message text.",
        route_id="account",
        context_json=None,
        app_version="atlas-web/0.1.0",
        schema_version="feedback/v1",
        contact_email="own@example.com",
        account_id=str(USER_ID),
    )
    assert own.status == "created"
    other = feedback.submit(
        submission_token=str(uuid4()),
        payload_fingerprint="b" * 64,
        category="general",
        message="Other account secret feedback text.",
        route_id="overview",
        context_json=None,
        app_version="atlas-web/0.1.0",
        schema_version="feedback/v1",
        contact_email="other@example.com",
        account_id=str(OTHER_USER),
    )
    assert other.status == "created"

    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "export"})
    confirmed = api.post(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": created.json()["confirmation_nonce"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "completed"
    assert all(
        item["system"] != "atlas-feedback-service"
        for item in confirmed.json()["omissions"]
    )
    download = api.get(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/export",
        headers=headers,
    )
    payload = download.json()
    rows = payload["data"]["feedback_contact"]
    assert len(rows) == 1
    assert rows[0]["category"] == "bug"
    assert rows[0]["route_id"] == "account"
    assert rows[0]["message"] == "Own account feedback message text."
    assert rows[0]["contact_email_existed"] is True
    assert "received_at" in rows[0]
    assert "Other account secret feedback text." not in str(payload)
    assert "own@example.com" not in str(payload)
    assert any(
        source["system"] == "atlas-feedback-service" for source in payload["sources"]
    )


def test_deletion_redacts_feedback_linkage_and_keeps_message() -> None:
    feedback = MemoryFeedbackStore()
    api, _, admin, _ = _api(feedback_store=feedback)
    headers = {"Authorization": "Bearer test-token"}
    feedback.submit(
        submission_token=str(uuid4()),
        payload_fingerprint="c" * 64,
        category="usability",
        message="Keep this feedback message after deletion.",
        route_id="privacy",
        context_json=None,
        app_version="atlas-web/0.1.0",
        schema_version="feedback/v1",
        contact_email="delete-me@example.com",
        account_id=str(USER_ID),
    )
    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "deletion"})
    confirmed = api.post(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": created.json()["confirmation_nonce"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "completed"
    assert admin.deleted == [USER_ID]
    assert feedback.redact_calls == 1
    row = next(iter(feedback._rows.values()))
    assert row.message == "Keep this feedback message after deletion."
    assert row.contact_email is None
    assert row.account_id is None
    assert "atlas-feedback-service" not in {
        item["system"] for item in confirmed.json()["omissions"]
    }


def test_deletion_needs_support_when_feedback_redaction_fails() -> None:
    feedback = MemoryFeedbackStore()
    feedback.fail_redact = True
    api, _, admin, _ = _api(feedback_store=feedback)
    headers = {"Authorization": "Bearer test-token"}
    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "deletion"})
    confirmed = api.post(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": created.json()["confirmation_nonce"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "needs_support"
    assert admin.deleted == []
    assert feedback.redact_calls == 1


def test_cross_user_privacy_request_is_not_found() -> None:
    store = MemoryPrivacyRequestStore()
    owner_api, _, _, _ = _api(store=store)
    headers = {"Authorization": "Bearer test-token"}
    created = owner_api.post("/v1/me/privacy-requests", headers=headers, json={"action": "export"})
    request_id = created.json()["request_id"]
    nonce = created.json()["confirmation_nonce"]
    other, _, _, _ = _api(token_verifier=FakeTokenVerifier(OTHER_USER), store=store)
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
    api, _, _, _ = _api()
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
    api, _, _, _ = _api(token_verifier=stale)
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
    api, _, admin, _ = _api()
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
    api, _, admin, _ = _api(store=store)
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
    api, _, admin, _ = _api(store=store)
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
    api, _, _, _ = _api(auth_admin=admin)
    headers = {"Authorization": "Bearer test-token"}
    created = api.post("/v1/me/privacy-requests", headers=headers, json={"action": "deletion"})
    confirmed = api.post(
        f"/v1/me/privacy-requests/{created.json()['request_id']}/confirm",
        headers=headers,
        json={"nonce": created.json()["confirmation_nonce"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "needs_support"
