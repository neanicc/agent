from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from loopguard_api.authorization import Principal, ROLE_PERMISSIONS
from loopguard_api.support import (
    SupportAccessRejected,
    SupportAccessService,
    SupportScope,
)


NOW = datetime(2026, 7, 30, tzinfo=UTC)


def _principal(tenant_id: uuid.UUID, role: str = "owner") -> Principal:
    return Principal(
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
        subject="tenant-admin",
        role=role,
        permissions=ROLE_PERMISSIONS[role],
    )


def test_support_access_requires_tenant_consent_and_exact_preview() -> None:
    tenant = uuid.uuid4()
    service = SupportAccessService(clock=lambda: NOW)
    input_value = {
        "health": {"status": "degraded", "error_code": "LGAPI-UPSTREAM"},
        "email": "developer@example.com",
        "source": "print('private')",
        "provider_token": "abcdefghijklmnopqrstuvwxyz012345",
    }
    consent = service.request_tenant_consent(
        _principal(tenant),
        scopes=frozenset(
            {SupportScope.METADATA_HEALTH, SupportScope.DIAGNOSTIC_BUNDLE}
        ),
        reason="Investigate relay health",
        ticket_id="SUP-123",
        lifetime=timedelta(hours=1),
        diagnostic_input=input_value,
    )
    session = service.open_session(consent.id, support_actor="support@example.com")
    bundle = service.diagnostic_bundle(session.id, input_value)
    assert "source" not in bundle
    assert "provider_token" not in bundle
    assert "developer@example.com" not in str(bundle)
    with pytest.raises(SupportAccessRejected, match="differs"):
        service.diagnostic_bundle(session.id, {**input_value, "new": "field"})


def test_support_metadata_is_allowlisted_and_cannot_execute_actions() -> None:
    tenant = uuid.uuid4()
    service = SupportAccessService(clock=lambda: NOW)
    consent = service.request_tenant_consent(
        _principal(tenant),
        scopes=frozenset({SupportScope.METADATA_HEALTH}),
        reason="Health review",
        ticket_id="SUP-124",
        lifetime=timedelta(minutes=30),
    )
    session = service.open_session(consent.id, support_actor="support@example.com")
    metadata = service.view_metadata(
        session.id,
        {"status": "degraded", "source": "private", "arbitrary": "not allowed"},
    )
    assert metadata == {"status": "degraded"}
    with pytest.raises(SupportAccessRejected, match="cannot"):
        service.execute_action(session.id, {"kind": "inject"})


def test_break_glass_requires_distinct_dual_approval_and_alerts_tenant() -> None:
    tenant = uuid.uuid4()
    alerts: list[tuple[uuid.UUID, str]] = []
    service = SupportAccessService(
        clock=lambda: NOW,
        tenant_alert=lambda tenant_id, message: alerts.append((tenant_id, message)),
    )
    consent = service.request_break_glass(
        tenant,
        requested_by="incident-commander@example.com",
        reason="Control plane incident",
        ticket_id="INC-42",
    )
    with pytest.raises(SupportAccessRejected, match="two approvals"):
        service.open_session(consent.id, support_actor="support@example.com")
    with pytest.raises(SupportAccessRejected, match="distinct"):
        service.approve_break_glass(
            consent.id,
            approved_by="incident-commander@example.com",
        )
    service.approve_break_glass(consent.id, approved_by="security@example.com")
    session = service.open_session(consent.id, support_actor="support@example.com")
    assert SupportScope.BREAK_GLASS in session.scopes
    assert alerts and alerts[0][0] == tenant


def test_revocation_and_expiry_close_existing_support_session() -> None:
    now = [NOW]
    service = SupportAccessService(clock=lambda: now[0])
    consent = service.request_tenant_consent(
        _principal(uuid.uuid4()),
        scopes=frozenset({SupportScope.METADATA_HEALTH}),
        reason="Short review",
        ticket_id="SUP-125",
        lifetime=timedelta(minutes=5),
    )
    session = service.open_session(consent.id, support_actor="support@example.com")
    service.revoke(consent.id, revoked_by="tenant-owner")
    with pytest.raises(SupportAccessRejected):
        service.view_metadata(session.id, {"status": "ok"})

    other = service.request_tenant_consent(
        _principal(uuid.uuid4()),
        scopes=frozenset({SupportScope.METADATA_HEALTH}),
        reason="Short review",
        ticket_id="SUP-126",
        lifetime=timedelta(minutes=5),
    )
    expired = service.open_session(other.id, support_actor="support@example.com")
    now[0] += timedelta(minutes=6)
    with pytest.raises(SupportAccessRejected):
        service.view_metadata(expired.id, {"status": "ok"})


def test_viewer_cannot_grant_support_consent() -> None:
    service = SupportAccessService(clock=lambda: NOW)
    with pytest.raises(SupportAccessRejected):
        service.request_tenant_consent(
            _principal(uuid.uuid4(), "viewer"),
            scopes=frozenset({SupportScope.METADATA_HEALTH}),
            reason="No authority",
            ticket_id="SUP-127",
            lifetime=timedelta(minutes=30),
        )
