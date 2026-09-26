"""User feedback submission store and single-process idempotency lock."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from lyme_gap_atlas_shared.settings import SnowflakeSettings
from lyme_gap_atlas_shared.snowflake import connect

from .models import FeedbackSubmissionRequest, FeedbackSubmissionResponse

logger = logging.getLogger(__name__)

FEEDBACK_SCHEMA_VERSION = "feedback/v1"
FEEDBACK_PERSISTENCE_DETAIL = "Feedback cannot be stored right now. Please try again later."
FEEDBACK_IDEMPOTENCY_MISMATCH_TYPE = (
    "https://carawaylabs.com/problems/feedback-idempotency-mismatch"
)
FEEDBACK_TOPOLOGY_UNSAFE_DETAIL = (
    "Feedback submission is unavailable because this API process is not configured "
    "for single-process idempotency."
)

_WORKER_ENV_KEYS = ("WEB_CONCURRENCY", "UVICORN_WORKERS")
_LOCK_STRIPE_COUNT = 128


class FeedbackStoreError(Exception):
    """Raised when the feedback store cannot complete a submission safely."""


class FeedbackIdempotencyMismatchError(Exception):
    """Raised when a submission token is reused with a different payload."""


class FeedbackRejectedError(Exception):
    """Raised when the store rejects arguments after API validation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class FeedbackStoreResult:
    status: Literal["created", "replayed", "mismatch", "rejected"]
    feedback_id: UUID | None = None
    received_at: datetime | None = None
    reason: str | None = None


class FeedbackStore(Protocol):
    def submit(
        self,
        *,
        submission_token: str,
        payload_fingerprint: str,
        category: str,
        message: str,
        route_id: str,
        context_json: str | None,
        app_version: str,
        schema_version: str,
        contact_email: str | None,
        account_id: str | None,
    ) -> FeedbackStoreResult: ...


def feedback_process_topology_safe(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return True when the process topology still matches process-local locking."""

    env = os.environ if environ is None else environ
    for key in _WORKER_ENV_KEYS:
        raw = env.get(key)
        if raw is None:
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            value = int(stripped)
        except ValueError:
            continue
        if value > 1:
            return False
    return True


def compute_payload_fingerprint(
    request: FeedbackSubmissionRequest,
    account_id: UUID | None,
) -> str:
    """SHA-256 hex of canonical JSON for accepted client fields plus account_id."""

    email = request.contact_email.casefold() if request.contact_email else None
    context = (
        None
        if request.context is None
        else request.context.model_dump(mode="json", exclude_none=True)
    )
    payload = {
        "account_id": str(account_id) if account_id is not None else None,
        "app_version": request.app_version,
        "category": request.category,
        "contact_email": email,
        "context": context,
        "message": unicodedata.normalize("NFC", request.message),
        "route_id": request.route_id,
        "submission_token": str(request.submission_token),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MemoryFeedbackStore:
    """In-process store for unit tests of the submission contract."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[str, UUID, datetime]] = {}
        self.calls = 0

    def submit(
        self,
        *,
        submission_token: str,
        payload_fingerprint: str,
        category: str,
        message: str,
        route_id: str,
        context_json: str | None,
        app_version: str,
        schema_version: str,
        contact_email: str | None,
        account_id: str | None,
    ) -> FeedbackStoreResult:
        del category, message, route_id, context_json, app_version, schema_version
        del contact_email, account_id
        self.calls += 1
        existing = self._rows.get(submission_token)
        if existing is not None:
            existing_fp, feedback_id, received_at = existing
            if existing_fp == payload_fingerprint:
                return FeedbackStoreResult(
                    status="replayed",
                    feedback_id=feedback_id,
                    received_at=received_at,
                )
            return FeedbackStoreResult(status="mismatch")
        feedback_id = uuid.uuid4()
        received_at = datetime.now(UTC)
        self._rows[submission_token] = (payload_fingerprint, feedback_id, received_at)
        return FeedbackStoreResult(
            status="created",
            feedback_id=feedback_id,
            received_at=received_at,
        )


class SnowflakeFeedbackStore:
    """Procedure-only feedback insert through GOVERNANCE.SP_SUBMIT_USER_FEEDBACK."""

    def __init__(self, settings: SnowflakeSettings) -> None:
        self._settings = settings

    def submit(
        self,
        *,
        submission_token: str,
        payload_fingerprint: str,
        category: str,
        message: str,
        route_id: str,
        context_json: str | None,
        app_version: str,
        schema_version: str,
        contact_email: str | None,
        account_id: str | None,
    ) -> FeedbackStoreResult:
        database = self._settings.snowflake_database
        try:
            with connect(self._settings) as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"CALL {database}.GOVERNANCE.SP_SUBMIT_USER_FEEDBACK"
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        submission_token,
                        payload_fingerprint,
                        category,
                        message,
                        route_id,
                        context_json,
                        app_version,
                        schema_version,
                        contact_email,
                        account_id,
                    ),
                )
                row = cursor.fetchone()
        except Exception as exc:
            raise FeedbackStoreError("feedback store call failed") from exc
        if row is None:
            raise FeedbackStoreError("feedback store returned no result")
        payload = row[0] if isinstance(row[0], dict) else json.loads(str(row[0]))
        if not isinstance(payload, dict):
            raise FeedbackStoreError("feedback store returned an invalid result")
        status = str(payload.get("status", ""))
        if status == "mismatch":
            return FeedbackStoreResult(status="mismatch")
        if status == "rejected":
            return FeedbackStoreResult(
                status="rejected",
                reason=str(payload.get("reason") or "rejected"),
            )
        if status not in {"created", "replayed"}:
            raise FeedbackStoreError("feedback store returned an unexpected status")
        feedback_id_raw = payload.get("feedback_id")
        received_raw = payload.get("received_at")
        if feedback_id_raw is None or received_raw is None:
            raise FeedbackStoreError("feedback store omitted required fields")
        received_at = (
            received_raw
            if isinstance(received_raw, datetime)
            else datetime.fromisoformat(str(received_raw).replace("Z", "+00:00"))
        )
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=UTC)
        return FeedbackStoreResult(
            status=status,  # type: ignore[arg-type]
            feedback_id=UUID(str(feedback_id_raw)),
            received_at=received_at,
        )


class FeedbackService:
    """Validate fingerprints and serialize same-token submits in one process.

    Locks come from a fixed stripe pool. Identical tokens always share a stripe.
    Unrelated tokens may share one. The pool does not grow with public traffic.
    """

    def __init__(self, store: FeedbackStore, *, lock_stripes: int = _LOCK_STRIPE_COUNT) -> None:
        if lock_stripes < 1:
            raise ValueError("lock_stripes must be positive")
        self._store = store
        self._lock_stripes = tuple(threading.Lock() for _ in range(lock_stripes))

    def _lock_for(self, submission_token: str) -> threading.Lock:
        digest = hashlib.sha256(submission_token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(self._lock_stripes)
        return self._lock_stripes[index]

    def submit(
        self,
        request: FeedbackSubmissionRequest,
        account_id: UUID | None,
    ) -> FeedbackSubmissionResponse:
        fingerprint = compute_payload_fingerprint(request, account_id)
        context_json = (
            None
            if request.context is None
            else json.dumps(
                request.context.model_dump(mode="json", exclude_none=True),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        token = str(request.submission_token)
        with self._lock_for(token):
            result = self._store.submit(
                submission_token=token,
                payload_fingerprint=fingerprint,
                category=request.category,
                message=request.message,
                route_id=request.route_id,
                context_json=context_json,
                app_version=request.app_version,
                schema_version=FEEDBACK_SCHEMA_VERSION,
                contact_email=request.contact_email,
                account_id=str(account_id) if account_id is not None else None,
            )
        if result.status == "mismatch":
            raise FeedbackIdempotencyMismatchError()
        if result.status == "rejected":
            raise FeedbackRejectedError(result.reason or "rejected")
        if result.feedback_id is None or result.received_at is None:
            raise FeedbackStoreError("feedback store omitted required fields")
        return FeedbackSubmissionResponse(
            feedback_id=result.feedback_id,
            received_at=result.received_at,
            replayed=result.status == "replayed",
        )
