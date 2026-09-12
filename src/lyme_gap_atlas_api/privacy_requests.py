"""Authenticated export and deletion request ledger (ADR 0016)."""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import httpx

from .auth_admin import AuthAdmin, AuthAdminError, AuthUserSnapshot
from .config import ApiSettings
from .models import (
    PrivacyAction,
    PrivacyOmission,
    PrivacyRequestCreated,
    PrivacyRequestState,
    PrivacyRequestStatus,
    UserDataExportEnvelope,
    UserProfile,
)
from .profiles import ProfileStore, ProfileStoreError

logger = logging.getLogger(__name__)

NONCE_TTL = timedelta(minutes=15)
EXPORT_TTL = timedelta(hours=1)
FRESH_SESSION = timedelta(minutes=15)
STALE_IN_PROGRESS = timedelta(minutes=5)
EXPORT_SCHEMA: Literal["atlas-user-data-export/v1"] = "atlas-user-data-export/v1"
ALLOWED_ERROR_CLASSES = frozenset(
    {
        "upstream_http",
        "auth_admin_unavailable",
        "invalid_state",
        "processor_failure",
        "stale_in_progress",
    }
)

STANDARD_OMISSIONS = (
    PrivacyOmission(
        system="atlas-personalization-service",
        reason="not_connected",
        detail="Saved views and pinned jurisdictions are not launched.",
    ),
    PrivacyOmission(
        system="atlas-feedback-service",
        reason="not_connected",
        detail="In-product feedback contact storage is not launched.",
    ),
    PrivacyOmission(
        system="amplitude",
        reason="not_account_linked",
        detail="Product analytics are session-scoped and cannot be looked up by account.",
    ),
    PrivacyOmission(
        system="browser-preferences",
        reason="browser_only",
        detail="Analytics choice and local chat history stay in the requesting browser.",
    ),
)


class PrivacyRequestStoreError(RuntimeError):
    def __init__(self, operation: str, category: str, upstream_status: int | None = None) -> None:
        super().__init__("Privacy request ledger is unavailable")
        self.operation = operation
        self.category = category
        self.upstream_status = upstream_status


@dataclass
class PrivacyRequestRecord:
    request_id: UUID
    user_id: UUID
    action: PrivacyAction
    state: PrivacyRequestState
    nonce_hash: str | None
    nonce_expires_at: datetime | None
    created_at: datetime
    confirmed_at: datetime | None = None
    completed_at: datetime | None = None
    export_payload: dict[str, Any] | None = None
    export_expires_at: datetime | None = None
    processor_outcomes: list[dict[str, Any]] = field(default_factory=list)
    error_class: str | None = None


class PrivacyRequestStore(Protocol):
    def create(self, record: PrivacyRequestRecord) -> PrivacyRequestRecord: ...

    def get(self, request_id: UUID) -> PrivacyRequestRecord | None: ...

    def save(self, record: PrivacyRequestRecord) -> PrivacyRequestRecord: ...

    def claim_for_confirm(
        self,
        request_id: UUID,
        user_id: UUID,
        nonce_hash: str,
        moment: datetime,
    ) -> PrivacyRequestRecord | None: ...

    def clear_export_payloads(self, user_id: UUID) -> None: ...


class MemoryPrivacyRequestStore:
    def __init__(self) -> None:
        self.records: dict[UUID, PrivacyRequestRecord] = {}

    def create(self, record: PrivacyRequestRecord) -> PrivacyRequestRecord:
        self.records[record.request_id] = record
        return record

    def get(self, request_id: UUID) -> PrivacyRequestRecord | None:
        return self.records.get(request_id)

    def save(self, record: PrivacyRequestRecord) -> PrivacyRequestRecord:
        self.records[record.request_id] = record
        return record

    def claim_for_confirm(
        self,
        request_id: UUID,
        user_id: UUID,
        nonce_hash: str,
        moment: datetime,
    ) -> PrivacyRequestRecord | None:
        record = self.records.get(request_id)
        if (
            record is None
            or record.user_id != user_id
            or record.state not in {"requested", "verified"}
            or not record.nonce_hash
            or record.nonce_hash != nonce_hash
            or record.nonce_expires_at is None
            or record.nonce_expires_at <= moment
        ):
            return None
        record.state = "in_progress"
        record.confirmed_at = moment
        record.nonce_hash = None
        record.nonce_expires_at = None
        self.records[request_id] = record
        return record

    def clear_export_payloads(self, user_id: UUID) -> None:
        for record in self.records.values():
            if record.user_id == user_id:
                record.export_payload = None
                record.export_expires_at = None


class SupabasePrivacyRequestStore:
    def __init__(self, settings: ApiSettings, client: httpx.Client | None = None) -> None:
        secret = settings.supabase_secret_key
        if not settings.supabase_url or secret is None:
            raise ValueError("Supabase privacy-request configuration is incomplete")
        self._url = f"{settings.supabase_url.rstrip('/')}/rest/v1/privacy_requests"
        self._secret = secret.get_secret_value()
        self._client = client or httpx.Client(timeout=10.0)

    def create(self, record: PrivacyRequestRecord) -> PrivacyRequestRecord:
        payload = self._row(record)
        response = self._request(
            "POST",
            headers={"Prefer": "return=representation"},
            json=payload,
        )
        return self._from_row(self._one(response, "create"))

    def get(self, request_id: UUID) -> PrivacyRequestRecord | None:
        response = self._request(
            "GET",
            params={"request_id": f"eq.{request_id}", "select": "*"},
        )
        rows = self._list(response, "get")
        if not rows:
            return None
        return self._from_row(rows[0])

    def save(self, record: PrivacyRequestRecord) -> PrivacyRequestRecord:
        response = self._request(
            "PATCH",
            headers={"Prefer": "return=representation"},
            params={"request_id": f"eq.{record.request_id}"},
            json=self._row(record),
        )
        return self._from_row(self._one(response, "save"))

    def claim_for_confirm(
        self,
        request_id: UUID,
        user_id: UUID,
        nonce_hash: str,
        moment: datetime,
    ) -> PrivacyRequestRecord | None:
        response = self._request(
            "PATCH",
            headers={"Prefer": "return=representation"},
            params={
                "request_id": f"eq.{request_id}",
                "user_id": f"eq.{user_id}",
                "nonce_hash": f"eq.{nonce_hash}",
                "state": "in.(requested,verified)",
                "nonce_expires_at": f"gt.{moment.isoformat()}",
            },
            json={
                "state": "in_progress",
                "confirmed_at": _iso(moment),
                "nonce_hash": None,
                "nonce_expires_at": None,
            },
        )
        rows = self._list(response, "claim_for_confirm")
        if not rows:
            return None
        return self._from_row(rows[0])

    def clear_export_payloads(self, user_id: UUID) -> None:
        self._request(
            "PATCH",
            headers={"Prefer": "return=minimal"},
            params={"user_id": f"eq.{user_id}", "export_payload": "not.is.null"},
            json={"export_payload": None, "export_expires_at": None},
        )

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
            upstream_status = (
                exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            )
            logger.warning(
                "privacy_request_store_failed",
                extra={
                    "context": {
                        "operation": method.lower(),
                        "failure_category": "upstream_http",
                        "upstream_status": upstream_status,
                    }
                },
            )
            raise PrivacyRequestStoreError(
                operation=method.lower(),
                category="upstream_http",
                upstream_status=upstream_status,
            ) from exc

    @staticmethod
    def _list(response: httpx.Response, operation: str) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PrivacyRequestStoreError(operation=operation, category="invalid_json") from exc
        if not isinstance(payload, list):
            raise PrivacyRequestStoreError(operation=operation, category="invalid_response_shape")
        return payload

    def _one(self, response: httpx.Response, operation: str) -> dict[str, Any]:
        rows = self._list(response, operation)
        if len(rows) != 1:
            raise PrivacyRequestStoreError(
                operation=operation, category="unexpected_response_count"
            )
        return rows[0]

    @staticmethod
    def _row(record: PrivacyRequestRecord) -> dict[str, Any]:
        return {
            "request_id": str(record.request_id),
            "user_id": str(record.user_id),
            "action": record.action,
            "state": record.state,
            "nonce_hash": record.nonce_hash,
            "nonce_expires_at": _iso(record.nonce_expires_at),
            "created_at": _iso(record.created_at),
            "confirmed_at": _iso(record.confirmed_at),
            "completed_at": _iso(record.completed_at),
            "export_payload": record.export_payload,
            "export_expires_at": _iso(record.export_expires_at),
            "processor_outcomes": record.processor_outcomes,
            "error_class": record.error_class,
        }

    @staticmethod
    def _from_row(row: dict[str, Any]) -> PrivacyRequestRecord:
        return PrivacyRequestRecord(
            request_id=UUID(str(row["request_id"])),
            user_id=UUID(str(row["user_id"])),
            action=row["action"],
            state=row["state"],
            nonce_hash=row.get("nonce_hash"),
            nonce_expires_at=_parse_datetime(row.get("nonce_expires_at")),
            created_at=_parse_datetime(row["created_at"]) or datetime.now(UTC),
            confirmed_at=_parse_datetime(row.get("confirmed_at")),
            completed_at=_parse_datetime(row.get("completed_at")),
            export_payload=row.get("export_payload")
            if isinstance(row.get("export_payload"), dict)
            else None,
            export_expires_at=_parse_datetime(row.get("export_expires_at")),
            processor_outcomes=list(row.get("processor_outcomes") or []),
            error_class=row.get("error_class"),
        )


class PrivacyRequestService:
    def __init__(
        self,
        store: PrivacyRequestStore,
        profile_store: ProfileStore,
        auth_admin: AuthAdmin,
    ) -> None:
        self._store = store
        self._profiles = profile_store
        self._auth = auth_admin

    def create(
        self, user_id: UUID, action: PrivacyAction, now: datetime | None = None
    ) -> PrivacyRequestCreated:
        moment = now or datetime.now(UTC)
        nonce = secrets.token_urlsafe(32)
        record = PrivacyRequestRecord(
            request_id=uuid4(),
            user_id=user_id,
            action=action,
            state="requested",
            nonce_hash=_hash_nonce(nonce),
            nonce_expires_at=moment + NONCE_TTL,
            created_at=moment,
        )
        saved = self._store.create(record)
        return PrivacyRequestCreated(
            **self._status(saved, moment).model_dump(),
            confirmation_nonce=nonce,
        )

    def confirm(
        self,
        request_id: UUID,
        user_id: UUID,
        nonce: str,
        issued_at: datetime,
        now: datetime | None = None,
    ) -> PrivacyRequestStatus:
        moment = now or datetime.now(UTC)
        if moment - issued_at > FRESH_SESSION:
            raise StaleSessionError
        owned = self._owned(request_id, user_id)
        if owned.state not in {"requested", "verified"}:
            raise InvalidPrivacyRequestError("invalid_state")
        if (
            not owned.nonce_hash
            or owned.nonce_expires_at is None
            or owned.nonce_expires_at <= moment
            or owned.nonce_hash != _hash_nonce(nonce)
        ):
            raise InvalidPrivacyRequestError("expired_or_replayed_nonce")
        record = self._store.claim_for_confirm(
            request_id=request_id,
            user_id=user_id,
            nonce_hash=owned.nonce_hash,
            moment=moment,
        )
        if record is None:
            raise InvalidPrivacyRequestError("expired_or_replayed_nonce")
        try:
            self._execute(record, moment)
        except (ProfileStoreError, AuthAdminError, PrivacyRequestStoreError) as exc:
            if _deletion_already_applied(record):
                logger.error(
                    "privacy_request_ledger_save_after_delete_failed",
                    extra={
                        "context": {
                            "request_id": str(record.request_id),
                            "action": record.action,
                            "failure_category": _redacted_error_class(exc),
                        }
                    },
                )
                return self._status(record, moment)
            record.state = "needs_support"
            record.error_class = _redacted_error_class(exc)
            record.processor_outcomes.append(
                {
                    "processor": "atlas-account-service",
                    "outcome": "failed",
                    "at": moment.isoformat(),
                }
            )
            logger.warning(
                "privacy_request_processor_failed",
                extra={
                    "context": {
                        "request_id": str(record.request_id),
                        "action": record.action,
                        "processor": "atlas-account-service",
                        "failure_category": record.error_class,
                    }
                },
            )
            record = self._store.save(record)
            return self._status(record, moment)
        return self._status(self._store.get(record.request_id) or record, moment)

    def status(
        self, request_id: UUID, user_id: UUID, now: datetime | None = None
    ) -> PrivacyRequestStatus:
        moment = now or datetime.now(UTC)
        record = self._owned(request_id, user_id)
        if (
            record.state == "in_progress"
            and record.confirmed_at
            and moment - record.confirmed_at > STALE_IN_PROGRESS
        ):
            record.state = "needs_support"
            record.error_class = "stale_in_progress"
            record = self._store.save(record)
        return self._status(record, moment)

    def export_payload(
        self, request_id: UUID, user_id: UUID, now: datetime | None = None
    ) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        record = self._owned(request_id, user_id)
        if (
            record.action != "export"
            or record.state != "completed"
            or not record.export_payload
            or record.export_expires_at is None
            or record.export_expires_at <= moment
        ):
            raise InvalidPrivacyRequestError("export_unavailable")
        return record.export_payload

    def _execute(self, record: PrivacyRequestRecord, moment: datetime) -> None:
        if record.action == "export":
            profile = self._profiles.get(record.user_id)
            snapshot = self._auth.get_user(record.user_id)
            envelope = _export_envelope(record, profile, snapshot, moment)
            record.export_payload = envelope.model_dump(mode="json")
            record.export_expires_at = moment + EXPORT_TTL
            record.processor_outcomes.append(
                {
                    "processor": "atlas-account-service",
                    "outcome": "completed",
                    "at": moment.isoformat(),
                }
            )
        else:
            self._auth.delete_user(record.user_id)
            record.export_payload = None
            record.export_expires_at = None
            record.processor_outcomes.append(
                {
                    "processor": "atlas-account-service",
                    "outcome": "deleted",
                    "at": moment.isoformat(),
                }
            )
            self._store.clear_export_payloads(record.user_id)
        record.state = "completed"
        record.completed_at = moment
        self._store.save(record)

    def _owned(self, request_id: UUID, user_id: UUID) -> PrivacyRequestRecord:
        record = self._store.get(request_id)
        if record is None or record.user_id != user_id:
            raise PrivacyRequestNotFoundError
        return record

    @staticmethod
    def _status(record: PrivacyRequestRecord, moment: datetime) -> PrivacyRequestStatus:
        download_available = bool(
            record.action == "export"
            and record.state == "completed"
            and record.export_payload
            and record.export_expires_at
            and record.export_expires_at > moment
        )
        return PrivacyRequestStatus(
            request_id=str(record.request_id),
            action=record.action,
            state=record.state,
            created_at=record.created_at,
            completed_at=record.completed_at,
            download_available=download_available,
            omissions=list(STANDARD_OMISSIONS),
            support_reason="A processor could not finish this request."
            if record.state == "needs_support"
            else None,
            confirmation_expires_at=record.nonce_expires_at,
        )


class PrivacyRequestNotFoundError(LookupError):
    pass


class InvalidPrivacyRequestError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class StaleSessionError(PermissionError):
    pass


def session_is_fresh(issued_at: datetime, now: datetime | None = None) -> bool:
    moment = now or datetime.now(UTC)
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=UTC)
    return moment - issued_at <= FRESH_SESSION


def _export_envelope(
    record: PrivacyRequestRecord,
    profile: UserProfile | None,
    snapshot: AuthUserSnapshot,
    moment: datetime,
) -> UserDataExportEnvelope:
    return UserDataExportEnvelope(
        schema_version=EXPORT_SCHEMA,
        generated_at=moment,
        request_id=str(record.request_id),
        subject={"account_id": str(record.user_id)},
        sources=[
            {
                "system": "atlas-account-service",
                "retrieved_at": moment.isoformat(),
            }
        ],
        data={
            "account": {
                "email": snapshot.email,
                "created_at": _iso(snapshot.created_at),
                "last_sign_in_at": _iso(snapshot.last_sign_in_at),
            },
            "profile": profile.model_dump() if profile else None,
            "personalization": [],
            "preferences": {},
            "feedback_contact": [],
            "analytics": [],
        },
        omissions=list(STANDARD_OMISSIONS),
    )


def _hash_nonce(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def _redacted_error_class(exc: BaseException) -> str:
    category = getattr(exc, "category", None)
    if isinstance(category, str) and category in ALLOWED_ERROR_CLASSES:
        return category
    if isinstance(category, str) and category in {
        "invalid_json",
        "invalid_response_shape",
        "unexpected_response_count",
    }:
        return "auth_admin_unavailable"
    return "processor_failure"


def _deletion_already_applied(record: PrivacyRequestRecord) -> bool:
    return record.action == "deletion" and any(
        outcome.get("processor") == "atlas-account-service"
        and outcome.get("outcome") == "deleted"
        for outcome in record.processor_outcomes
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None
