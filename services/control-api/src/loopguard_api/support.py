from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from .audit import AuditStore
from .authorization import Principal


class SupportAccessRejected(ValueError):
    pass


class SupportScope(StrEnum):
    METADATA_HEALTH = "metadata_health"
    DIAGNOSTIC_BUNDLE = "diagnostic_bundle"
    BREAK_GLASS = "break_glass"


_FORBIDDEN_KEYS = {
    "action",
    "approval",
    "artifact",
    "credential",
    "environment",
    "fixture",
    "patch",
    "prompt",
    "secret",
    "source",
    "stacktrace",
    "token",
}
_METADATA_KEYS = {
    "build_sha",
    "capabilities",
    "deployment_region",
    "environment",
    "error_code",
    "health",
    "host_count",
    "last_seen_at",
    "plan",
    "repository_count",
    "service",
    "status",
    "tenant_created_at",
    "version",
}
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")


@dataclass(frozen=True, slots=True)
class SupportConsent:
    id: uuid.UUID
    tenant_id: uuid.UUID
    requested_by: uuid.UUID | None
    reason: str
    ticket_id: str
    scopes: frozenset[SupportScope]
    diagnostic_preview: dict[str, Any] | None
    preview_hash: str | None
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    support_approvers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SupportSession:
    id: uuid.UUID
    consent_id: uuid.UUID
    tenant_id: uuid.UUID
    support_actor: str
    scopes: frozenset[SupportScope]
    reason: str
    ticket_id: str
    expires_at: datetime


class InMemorySupportStore:
    durable = False

    def __init__(self) -> None:
        self.consents: dict[uuid.UUID, SupportConsent] = {}
        self.sessions: dict[uuid.UUID, SupportSession] = {}
        self.lock = threading.RLock()


class SupportAccessService:
    def __init__(
        self,
        *,
        store: InMemorySupportStore | None = None,
        audit: AuditStore | None = None,
        tenant_alert: Callable[[uuid.UUID, str], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store or InMemorySupportStore()
        self.audit = audit or AuditStore()
        self.tenant_alert = tenant_alert or (lambda _tenant_id, _message: None)
        self.clock = clock

    @property
    def durable(self) -> bool:
        return bool(getattr(self.store, "durable", False))

    def request_tenant_consent(
        self,
        principal: Principal,
        *,
        scopes: frozenset[SupportScope],
        reason: str,
        ticket_id: str,
        lifetime: timedelta,
        diagnostic_input: Mapping[str, Any] | None = None,
    ) -> SupportConsent:
        if principal.role not in {"admin", "owner"}:
            raise SupportAccessRejected("support consent requires a tenant administrator")
        if not scopes or SupportScope.BREAK_GLASS in scopes:
            raise SupportAccessRejected("tenant consent scope is invalid")
        if not timedelta(minutes=5) <= lifetime <= timedelta(hours=24):
            raise SupportAccessRejected("support consent must expire within 5 minutes to 24 hours")
        preview = None
        preview_hash = None
        if SupportScope.DIAGNOSTIC_BUNDLE in scopes:
            if diagnostic_input is None:
                raise SupportAccessRejected("diagnostic consent requires a redaction preview")
            preview = self.redaction_preview(diagnostic_input)
            preview_hash = _digest(preview)
        now = self.clock()
        consent = SupportConsent(
            id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            requested_by=principal.user_id,
            reason=_bounded(reason, 512, "support reason"),
            ticket_id=_bounded(ticket_id, 128, "support ticket"),
            scopes=scopes,
            diagnostic_preview=preview,
            preview_hash=preview_hash,
            created_at=now,
            expires_at=now + lifetime,
        )
        with self.store.lock:
            self.store.consents[consent.id] = consent
        self._audit(consent, str(principal.user_id), "support.consent.created", "granted")
        return consent

    def request_break_glass(
        self,
        tenant_id: uuid.UUID,
        *,
        requested_by: str,
        reason: str,
        ticket_id: str,
        lifetime: timedelta = timedelta(minutes=30),
    ) -> SupportConsent:
        if not timedelta(minutes=5) <= lifetime <= timedelta(hours=1):
            raise SupportAccessRejected("break-glass access must expire within one hour")
        now = self.clock()
        consent = SupportConsent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            requested_by=None,
            reason=_bounded(reason, 512, "break-glass reason"),
            ticket_id=_bounded(ticket_id, 128, "break-glass ticket"),
            scopes=frozenset({SupportScope.METADATA_HEALTH, SupportScope.BREAK_GLASS}),
            diagnostic_preview=None,
            preview_hash=None,
            created_at=now,
            expires_at=now + lifetime,
            support_approvers=(_actor(requested_by),),
        )
        with self.store.lock:
            self.store.consents[consent.id] = consent
        self._audit(consent, requested_by, "support.break_glass.requested", "pending_second_approval")
        return consent

    def approve_break_glass(self, consent_id: uuid.UUID, *, approved_by: str) -> SupportConsent:
        approver = _actor(approved_by)
        with self.store.lock:
            current = self._active_consent(consent_id)
            if SupportScope.BREAK_GLASS not in current.scopes:
                raise SupportAccessRejected("consent is not break glass")
            if approver in current.support_approvers:
                raise SupportAccessRejected("break-glass approvers must be distinct")
            if len(current.support_approvers) != 1:
                raise SupportAccessRejected("break-glass approval state is invalid")
            updated = replace(
                current,
                support_approvers=(*current.support_approvers, approver),
            )
            self.store.consents[consent_id] = updated
        self.tenant_alert(
            updated.tenant_id,
            f"Break-glass support access approved for ticket {updated.ticket_id}; "
            f"expires {updated.expires_at.isoformat()}",
        )
        self._audit(updated, approver, "support.break_glass.approved", "granted")
        return updated

    def open_session(
        self,
        consent_id: uuid.UUID,
        *,
        support_actor: str,
    ) -> SupportSession:
        actor = _actor(support_actor)
        with self.store.lock:
            consent = self._active_consent(consent_id)
            if (
                SupportScope.BREAK_GLASS in consent.scopes
                and len(consent.support_approvers) != 2
            ):
                raise SupportAccessRejected("break-glass access requires two approvals")
            session = SupportSession(
                id=uuid.uuid4(),
                consent_id=consent.id,
                tenant_id=consent.tenant_id,
                support_actor=actor,
                scopes=consent.scopes,
                reason=consent.reason,
                ticket_id=consent.ticket_id,
                expires_at=consent.expires_at,
            )
            self.store.sessions[session.id] = session
        self._audit(consent, actor, "support.session.opened", "granted")
        return session

    def revoke(self, consent_id: uuid.UUID, *, revoked_by: str) -> None:
        with self.store.lock:
            current = self.store.consents.get(consent_id)
            if current is None:
                raise SupportAccessRejected("support consent does not exist")
            updated = replace(current, revoked_at=self.clock())
            self.store.consents[consent_id] = updated
        self._audit(updated, revoked_by, "support.consent.revoked", "completed")

    def view_metadata(
        self,
        session_id: uuid.UUID,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        session = self._active_session(session_id, SupportScope.METADATA_HEALTH)
        result = {
            key: _redact(value, depth=0)
            for key, value in values.items()
            if key.casefold() in _METADATA_KEYS
        }
        consent = self._active_consent(session.consent_id)
        self._audit(consent, session.support_actor, "support.metadata.viewed", "completed")
        return result

    def diagnostic_bundle(
        self,
        session_id: uuid.UUID,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        session = self._active_session(session_id, SupportScope.DIAGNOSTIC_BUNDLE)
        preview = self.redaction_preview(values)
        consent = self._active_consent(session.consent_id)
        if consent.preview_hash is None or not hmac.compare_digest(
            consent.preview_hash,
            _digest(preview),
        ):
            raise SupportAccessRejected("diagnostic bundle differs from tenant-approved preview")
        self._audit(consent, session.support_actor, "support.diagnostic.downloaded", "completed")
        return preview

    def execute_action(self, _session_id: uuid.UUID, _action: object) -> None:
        raise SupportAccessRejected("support sessions cannot approve or execute remote actions")

    def redaction_preview(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return _redact(values, depth=0)

    def _active_consent(self, consent_id: uuid.UUID) -> SupportConsent:
        consent = self.store.consents.get(consent_id)
        if (
            consent is None
            or consent.revoked_at is not None
            or self.clock() >= consent.expires_at
        ):
            raise SupportAccessRejected("support consent is unavailable or expired")
        return consent

    def _active_session(
        self,
        session_id: uuid.UUID,
        scope: SupportScope,
    ) -> SupportSession:
        session = self.store.sessions.get(session_id)
        if session is None or self.clock() >= session.expires_at:
            raise SupportAccessRejected("support session is unavailable or expired")
        self._active_consent(session.consent_id)
        if scope not in session.scopes:
            raise SupportAccessRejected("support session scope is insufficient")
        return session

    def _audit(
        self,
        consent: SupportConsent,
        actor: str,
        action: str,
        result: str,
    ) -> None:
        self.audit.append(
            tenant_id=consent.tenant_id,
            actor_id=_actor(actor),
            action=action,
            target_kind="support_consent",
            target_id=str(consent.id),
            request_id=f"support_{uuid.uuid4().hex}",
            before_state_hash=None,
            after_state_hash=hashlib.sha256(
                f"{consent.id}:{consent.expires_at.isoformat()}:{consent.revoked_at}".encode()
            ).hexdigest(),
            result=result,
            actor_metadata={
                "ticket_id": consent.ticket_id,
                "reason": consent.reason,
                "scopes": sorted(consent.scopes),
            },
        )


def _redact(value: Any, *, depth: int) -> Any:
    if depth > 8:
        raise SupportAccessRejected("diagnostic bundle is too deeply nested")
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:256]:
            key = str(raw_key)
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if any(forbidden in normalized for forbidden in _FORBIDDEN_KEYS):
                continue
            output[key[:128]] = _redact(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth=depth + 1) for item in value[:256]]
    if isinstance(value, str):
        text = _EMAIL.sub("[redacted-email]", value[:4_096])
        return _LONG_TOKEN.sub("[redacted-token]", text)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    raise SupportAccessRejected("diagnostic bundle contains an unsupported value")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _bounded(value: str, maximum: int, label: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise SupportAccessRejected(f"{label} is invalid")
    return normalized


def _actor(value: str) -> str:
    return _bounded(value, 256, "support actor")
