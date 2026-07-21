from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from loopguard_api.action_signing import Ed25519ActionSigner
from loopguard_api.actions import ActionService, DeviceProofRequired
from loopguard_api.authorization import Permission, Principal


NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def _principal() -> Principal:
    return Principal(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        subject="operator",
        role="operator",
        permissions=frozenset({Permission.VIEW_SESSION, Permission.CONTROL_SESSION}),
    )


def test_cloud_rejects_action_without_current_registered_device_proof():
    service = ActionService(
        signer=Ed25519ActionSigner.generate("cloud-key-1"), clock=lambda: NOW
    )
    challenge = service.create_challenge(
        principal=_principal(),
        requested_by_device_id=uuid.uuid4(),
        target_kind="session",
        target_id="session_1",
        host_id="host_1",
        kind="interrupt",
        parameters={},
        expected_state_version=4,
        expected_state_hash="sha256:state4",
        expires_in=30,
    )

    try:
        service.accept_signed(
            challenge.action_id,
            device_id=uuid.uuid4(),
            device_key_id="missing",
            device_algorithm="Ed25519",
            device_signature="invalid",
        )
    except DeviceProofRequired as exc:
        assert exc.code == "device_proof_required"
    else:
        raise AssertionError("missing device proof must fail")


def test_api_acceptance_is_queued_not_execution():
    principal = _principal()
    device_id = uuid.uuid4()
    device_key = Ed25519PrivateKey.generate()
    service = ActionService(
        signer=Ed25519ActionSigner.generate("cloud-key-1"), clock=lambda: NOW
    )
    service.register_device(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        device_id=device_id,
        key_id="device-key-1",
        algorithm="Ed25519",
        public_key=device_key.public_key(),
    )
    action = service.create_challenge(
        principal=principal,
        requested_by_device_id=device_id,
        target_kind="session",
        target_id="session_1",
        host_id="host_1",
        kind="interrupt",
        parameters={},
        expected_state_version=4,
        expected_state_hash="sha256:state4",
        expires_in=30,
    )
    signature = base64.urlsafe_b64encode(device_key.sign(action.canonical_bytes)).decode()

    queued = service.accept_signed(
        action.action_id,
        device_id=device_id,
        device_key_id="device-key-1",
        device_algorithm="Ed25519",
        device_signature=signature,
    )

    assert queued.state == "queued"
    assert queued.executed_at is None
    resolved = service.resolve_from_host(action.action_id, status="executed")
    assert resolved.state == "executed"
    assert resolved.executed_at == NOW


def test_expiry_and_device_revocation_during_review_fail():
    current = NOW
    principal = _principal()
    device_id = uuid.uuid4()
    device_key = Ed25519PrivateKey.generate()
    service = ActionService(
        signer=Ed25519ActionSigner.generate("cloud-key-1"), clock=lambda: current
    )
    service.register_device(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        device_id=device_id,
        key_id="device-key-1",
        algorithm="Ed25519",
        public_key=device_key.public_key(),
    )
    action = service.create_challenge(
        principal=principal,
        requested_by_device_id=device_id,
        target_kind="session",
        target_id="session_1",
        host_id="host_1",
        kind="interrupt",
        parameters={},
        expected_state_version=1,
        expected_state_hash="sha256:one",
        expires_in=30,
    )
    signature = base64.urlsafe_b64encode(device_key.sign(action.canonical_bytes)).decode()
    service.revoke_device(device_id)

    try:
        service.accept_signed(
            action.action_id,
            device_id=device_id,
            device_key_id="device-key-1",
            device_algorithm="Ed25519",
            device_signature=signature,
        )
    except DeviceProofRequired:
        pass
    else:
        raise AssertionError("revoked proof must fail")

    current += timedelta(seconds=31)
    assert service.expire(action.action_id).state == "expired"
