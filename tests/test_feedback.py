"""Contract tests for POST /v1/feedback (API story #42)."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_api import FakeRepository, FakeTokenVerifier

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.feedback import (
    FEEDBACK_IDEMPOTENCY_MISMATCH_TYPE,
    FEEDBACK_SCHEMA_VERSION,
    FeedbackService,
    FeedbackStoreError,
    MemoryFeedbackStore,
    compute_payload_fingerprint,
    feedback_process_topology_safe,
)
from lyme_gap_atlas_api.models import FeedbackSubmissionRequest

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


def _settings(**overrides: Any) -> ApiSettings:
    values = {
        "snowflake_account": "test",
        "snowflake_user": "test",
        "snowflake_role": "test",
        "snowflake_pat": "test",
        "cors_origins": ["https://carawaylabs.com"],
        "rate_limit_per_minute": 1_000,
    }
    values.update(overrides)
    return ApiSettings(**values)


def _valid_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "submission_token": str(uuid.uuid4()),
        "category": "general",
        "message": "The map legend is hard to read on mobile.",
        "route_id": "overview",
        "app_version": "atlas-web/0.1.0",
        "context": {
            "state": "CO",
            "county_fips": "08031",
            "explorer_view": "tiles",
            "explorer_metric": "score",
            "ecological_share": 65,
        },
    }
    body.update(overrides)
    return body


def _api(
    *,
    store: MemoryFeedbackStore | None = None,
    token_verifier: FakeTokenVerifier | None = None,
    settings: ApiSettings | None = None,
) -> tuple[TestClient, MemoryFeedbackStore]:
    feedback_store = store or MemoryFeedbackStore()
    configured = settings or _settings()
    kwargs: dict[str, Any] = {
        "repository": FakeRepository(),
        "settings": configured,
        "feedback_store": feedback_store,
    }
    if token_verifier is not None:
        kwargs["token_verifier"] = token_verifier
    return TestClient(create_app(**kwargs)), feedback_store


def test_anonymous_submission_returns_canonical_id() -> None:
    api, store = _api()
    response = api.post("/v1/feedback", json=_valid_body())
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.json()
    assert payload["replayed"] is False
    UUID(payload["feedback_id"])
    assert datetime.fromisoformat(payload["received_at"].replace("Z", "+00:00"))
    assert "message" not in payload
    assert "contact_email" not in payload
    assert "account_id" not in payload
    assert store.calls == 1


def test_authenticated_submission_uses_verifier_sub() -> None:
    captured: dict[str, str | None] = {}

    class CapturingStore(MemoryFeedbackStore):
        def submit(self, **kwargs: Any) -> Any:  # type: ignore[override]
            captured["account_id"] = kwargs["account_id"]
            return super().submit(**kwargs)

    capturing = CapturingStore()
    api, _ = _api(store=capturing, token_verifier=FakeTokenVerifier(USER_ID))
    response = api.post(
        "/v1/feedback",
        json=_valid_body(),
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert captured["account_id"] == str(USER_ID)


def test_missing_authorization_does_not_call_verifier() -> None:
    class RecordingVerifier(FakeTokenVerifier):
        def __init__(self) -> None:
            super().__init__(USER_ID)
            self.calls = 0

        def verify(self, authorization: str | None) -> Any:
            self.calls += 1
            return super().verify(authorization)

    verifier = RecordingVerifier()
    api, _ = _api(token_verifier=verifier)
    response = api.post("/v1/feedback", json=_valid_body())
    assert response.status_code == 200
    assert verifier.calls == 0


def test_garbage_bearer_returns_401_without_store_call() -> None:
    store = MemoryFeedbackStore()
    api, _ = _api(store=store, token_verifier=FakeTokenVerifier(USER_ID))
    response = api.post(
        "/v1/feedback",
        json=_valid_body(),
        headers={"Authorization": "Bearer not-valid"},
    )
    assert response.status_code == 401
    assert store.calls == 0


def test_bearer_without_supabase_returns_503_for_anonymous_still_works() -> None:
    api, _ = _api()
    anonymous = api.post("/v1/feedback", json=_valid_body())
    assert anonymous.status_code == 200
    presented = api.post(
        "/v1/feedback",
        json=_valid_body(),
        headers={"Authorization": "Bearer test-token"},
    )
    assert presented.status_code == 503


def test_client_feedback_id_and_account_id_rejected() -> None:
    api, _ = _api()
    for field, value in (
        ("feedback_id", str(uuid.uuid4())),
        ("received_at", datetime.now(UTC).isoformat()),
        ("account_id", str(USER_ID)),
    ):
        body = _valid_body(**{field: value})
        response = api.post("/v1/feedback", json=body)
        assert response.status_code == 422, field


def test_unknown_field_and_oversize_message_and_bad_fips() -> None:
    api, _ = _api()
    assert api.post("/v1/feedback", json=_valid_body(extra="nope")).status_code == 422
    assert (
        api.post("/v1/feedback", json=_valid_body(message="short")).status_code == 422
    )
    assert (
        api.post(
            "/v1/feedback",
            json=_valid_body(message="x" * 2001),
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/v1/feedback",
            json=_valid_body(context={"county_fips": "8031"}),
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/v1/feedback",
            json=_valid_body(context={"unexpected": "value"}),
        ).status_code
        == 422
    )


def test_model_rejects_too_many_selected_counties() -> None:
    with pytest.raises(ValidationError):
        FeedbackSubmissionRequest.model_validate(
            _valid_body(
                context={
                    "selected_county_fips": [
                        "08001",
                        "08003",
                        "08005",
                        "08007",
                        "08009",
                        "08011",
                    ]
                }
            )
        )


def test_replay_returns_same_id() -> None:
    api, _ = _api()
    body = _valid_body()
    first = api.post("/v1/feedback", json=body)
    second = api.post("/v1/feedback", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["feedback_id"] == second.json()["feedback_id"]
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True


def test_token_payload_mismatch_returns_409_problem() -> None:
    api, _ = _api()
    token = str(uuid.uuid4())
    first = api.post("/v1/feedback", json=_valid_body(submission_token=token))
    assert first.status_code == 200
    second = api.post(
        "/v1/feedback",
        json=_valid_body(
            submission_token=token,
            message="A completely different feedback message.",
        ),
    )
    assert second.status_code == 409
    problem = second.json()
    assert problem["type"] == FEEDBACK_IDEMPOTENCY_MISMATCH_TYPE
    assert problem["request_id"]
    assert "SELECT" not in problem["detail"]
    assert "CALL" not in problem["detail"]


def test_store_failure_returns_generic_503() -> None:
    class FailingStore(MemoryFeedbackStore):
        def submit(self, **kwargs: Any) -> Any:  # type: ignore[override]
            raise FeedbackStoreError("snowflake exploded with SQL")

    api, _ = _api(store=FailingStore())
    response = api.post("/v1/feedback", json=_valid_body())
    assert response.status_code == 503
    problem = response.json()
    assert "SQL" not in problem["detail"]
    assert "snowflake" not in problem["detail"].lower()
    assert problem["request_id"]


def test_feedback_submission_log_omits_sensitive_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    api, _ = _api(token_verifier=FakeTokenVerifier(USER_ID))
    message = "SECRET_FEEDBACK_MESSAGE_SHOULD_NOT_APPEAR"
    email = "secret.user@example.com"
    response = api.post(
        "/v1/feedback",
        json=_valid_body(message=message, contact_email=email),
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    rendered = capsys.readouterr().err
    assert "feedback_submission" in rendered
    assert message not in rendered
    assert email not in rendered
    assert "Bearer test-token" not in rendered


def test_idempotency_locks_stay_bounded() -> None:
    service = FeedbackService(MemoryFeedbackStore())
    assert len(service._lock_stripes) == 128
    tokens = [str(uuid.uuid4()) for _ in range(1000)]
    locks = [service._lock_for(token) for token in tokens]
    assert len({id(lock) for lock in locks}) <= 128
    assert service._lock_for(tokens[0]) is service._lock_for(tokens[0])


def test_concurrent_submits_share_one_canonical_id() -> None:
    store = MemoryFeedbackStore()
    service = FeedbackService(store)
    request = FeedbackSubmissionRequest.model_validate(_valid_body())
    results: list[Any] = []

    def _submit() -> None:
        results.append(service.submit(request, None))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_submit) for _ in range(2)]
        for future in futures:
            future.result()
    assert len(results) == 2
    assert results[0].feedback_id == results[1].feedback_id
    assert {result.replayed for result in results} == {False, True}
    assert store.calls == 2


def test_fingerprint_lowercases_email_only_inside_hash() -> None:
    body = _valid_body(contact_email="Person@Example.COM")
    request = FeedbackSubmissionRequest.model_validate(body)
    assert request.contact_email == "Person@Example.COM"
    first = compute_payload_fingerprint(request, None)
    second_request = FeedbackSubmissionRequest.model_validate(
        _valid_body(
            submission_token=str(request.submission_token),
            contact_email="person@example.com",
        )
    )
    second = compute_payload_fingerprint(second_request, None)
    assert first == second
    assert FEEDBACK_SCHEMA_VERSION == "feedback/v1"


def test_multi_worker_env_makes_feedback_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UVICORN_WORKERS", "2")
    assert feedback_process_topology_safe() is False
    store = MemoryFeedbackStore()
    api = TestClient(
        create_app(
            FakeRepository(),
            _settings(),
            feedback_store=store,
        )
    )
    ready = api.get("/health/ready")
    assert ready.status_code == 503
    response = api.post("/v1/feedback", json=_valid_body())
    assert response.status_code == 503
    assert store.calls == 0


def test_web_concurrency_above_one_is_unsafe() -> None:
    assert feedback_process_topology_safe({"WEB_CONCURRENCY": "2"}) is False
    assert feedback_process_topology_safe({"WEB_CONCURRENCY": "1"}) is True
    assert feedback_process_topology_safe({}) is True


def test_concurrent_http_submits_one_canonical_id() -> None:
    api, store = _api()
    body = _valid_body()
    barrier = threading.Barrier(2)
    outcomes: list[dict[str, Any]] = []

    def _post() -> None:
        barrier.wait()
        response = api.post("/v1/feedback", json=body)
        outcomes.append(response.json())

    threads = [threading.Thread(target=_post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(outcomes) == 2
    assert outcomes[0]["feedback_id"] == outcomes[1]["feedback_id"]
    assert store.calls >= 1
