from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA, SECP256R1, generate_private_key
from cryptography.hazmat.primitives.hashes import SHA256

from loopguard_api.action_signing import Ed25519ActionSigner
from loopguard_api.actions import ActionService, DeviceProofRequired
from loopguard_api.authorization import Permission, Principal
from loopguard_api.routes.actions import _action_view


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
    service = ActionService(signer=Ed25519ActionSigner.generate("cloud-key-1"), clock=lambda: NOW)
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
    service = ActionService(signer=Ed25519ActionSigner.generate("cloud-key-1"), clock=lambda: NOW)
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


def test_action_read_view_contains_exact_authenticated_review_material():
    principal = _principal()
    device_id = uuid.uuid4()
    service = ActionService(signer=Ed25519ActionSigner.generate("cloud-key-1"), clock=lambda: NOW)
    action = service.create_challenge(
        principal=principal,
        requested_by_device_id=device_id,
        target_kind="session",
        target_id="session_1",
        host_id="host_1",
        kind="inject",
        parameters={"message": "bounded intervention"},
        expected_state_version=7,
        expected_state_hash="sha256:state7",
        expires_in=30,
    )

    view = _action_view(action)

    assert view["parameters_hash"].startswith("sha256:")
    assert view["expected_state_version"] == 7
    assert view["expected_state_hash"] == "sha256:state7"
    assert view["nonce"] == action.nonce
    assert base64.urlsafe_b64decode(view["canonical_payload"]) == action.canonical_bytes
    assert view["risk"] == "high"
    assert view["requires_biometric"] is True
    assert "parameters" not in view


def test_read_reconciles_expired_review_without_waiting_for_delivery():
    current = NOW
    service = ActionService(
        signer=Ed25519ActionSigner.generate("cloud-key-1"),
        clock=lambda: current,
    )
    action = service.create_challenge(
        principal=_principal(),
        requested_by_device_id=uuid.uuid4(),
        target_kind="session",
        target_id="session_1",
        host_id="host_1",
        kind="continue_once",
        parameters={},
        expected_state_version=7,
        expected_state_hash="sha256:state7",
        expires_in=30,
    )
    current = NOW + timedelta(seconds=31)

    assert service.read(action.action_id).state == "expired"


def test_p256_secure_enclave_device_can_sign_exact_action_challenge():
    principal = _principal()
    device_id = uuid.uuid4()
    device_key = generate_private_key(SECP256R1())
    service = ActionService(signer=Ed25519ActionSigner.generate("cloud-key-1"), clock=lambda: NOW)
    service.register_device(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        device_id=device_id,
        key_id="secure-enclave-key-1",
        algorithm="P-256",
        public_key=device_key.public_key(),
    )
    action = service.create_challenge(
        principal=principal,
        requested_by_device_id=device_id,
        target_kind="session",
        target_id="session_1",
        host_id="host_1",
        kind="interrupt",
        parameters={"reason": "loop detected"},
        expected_state_version=5,
        expected_state_hash="sha256:state5",
        expires_in=30,
    )
    signature = base64.urlsafe_b64encode(
        device_key.sign(action.canonical_bytes, ECDSA(SHA256()))
    ).decode()

    queued = service.accept_signed(
        action.action_id,
        device_id=device_id,
        device_key_id="secure-enclave-key-1",
        device_algorithm="P-256",
        device_signature=signature,
    )

    assert queued.state == "queued"
    assert queued.device_algorithm == "P-256"


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
