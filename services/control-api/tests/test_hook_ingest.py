from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from loopguard_api.hook_ingest import HookRejected, HookService


NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def test_hook_signature_replay_and_repo_substitution_fail():
    service = HookService(clock=lambda: NOW)
    credential = service.issue(
        tenant_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        repository_handle="rh_1",
        expires_at=NOW + timedelta(hours=1),
    )
    body = b'{"event_id":"evt_1","kind":"tool.result"}'
    signed = service.sign_for_test(
        credential,
        method="POST",
        path="/v1/hook-events",
        timestamp=NOW,
        nonce="n1",
        repository_handle="rh_1",
        body=body,
    )

    accepted = service.verify(method="POST", path="/v1/hook-events", body=body, **signed)
    assert accepted.repository_handle == "rh_1"
    with pytest.raises(HookRejected, match="replay"):
        service.verify(method="POST", path="/v1/hook-events", body=body, **signed)

    substituted = service.sign_for_test(
        credential,
        method="POST",
        path="/v1/hook-events",
        timestamp=NOW,
        nonce="n2",
        repository_handle="rh_2",
        body=body,
    )
    with pytest.raises(HookRejected, match="repository binding"):
        service.verify(method="POST", path="/v1/hook-events", body=body, **substituted)


def test_expired_revoked_and_tampered_hooks_fail():
    current = NOW
    service = HookService(clock=lambda: current, maximum_body_bytes=64)
    credential = service.issue(
        tenant_id=uuid.uuid4(),
        host_id=None,
        repository_handle="rh_1",
        expires_at=NOW + timedelta(minutes=1),
    )
    signed = service.sign_for_test(
        credential,
        method="POST",
        path="/v1/hook-events",
        timestamp=NOW,
        nonce="nonce",
        repository_handle="rh_1",
        body=b"{}",
    )

    with pytest.raises(HookRejected, match="signature"):
        service.verify(
            method="POST", path="/v1/hook-events", body=b'{"changed":true}', **signed
        )
    current += timedelta(minutes=2)
    with pytest.raises(HookRejected, match="expired"):
        service.verify(method="POST", path="/v1/hook-events", body=b"{}", **signed)

    current = NOW
    second = service.issue(
        tenant_id=uuid.uuid4(),
        host_id=None,
        repository_handle="rh_2",
        expires_at=NOW + timedelta(minutes=1),
    )
    service.revoke(second.key_id)
    revoked = service.sign_for_test(
        second,
        method="POST",
        path="/v1/hook-events",
        timestamp=NOW,
        nonce="n2",
        repository_handle="rh_2",
        body=b"{}",
    )
    with pytest.raises(HookRejected, match="revoked"):
        service.verify(method="POST", path="/v1/hook-events", body=b"{}", **revoked)
