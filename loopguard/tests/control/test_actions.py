from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from loopguard.control.actions import (
    ActionVerifier,
    SignedActionRequest,
    canonical_action_bytes,
)
from loopguard.control.decisions import ActionKind, ActionTarget, TargetKind


NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def _signed_action(**updates: object):
    device_private = Ed25519PrivateKey.generate()
    cloud_private = Ed25519PrivateKey.generate()
    values = {
        "action_id": "act_1",
        "target": ActionTarget(kind=TargetKind.SESSION, target_id="session_1"),
        "kind": ActionKind.INTERRUPT,
        "parameters": {},
        "expected_state_version": 4,
        "expected_state_hash": "sha256:state4",
        "nonce": "nonce_1",
        "expires_at": NOW + timedelta(seconds=30),
        "tenant_id": "tenant_1",
        "host_id": "host_1",
        "requested_by": "user_1",
        "requested_by_device_id": "device_1",
        "issued_at": NOW,
        "device_key_id": "device-key-1",
        "device_algorithm": "Ed25519",
        "device_signature": "",
        "cloud_key_id": "cloud-key-1",
        "cloud_algorithm": "Ed25519",
        "cloud_signature": "",
        "canonicalization_version": 1,
    }
    values.update(updates)
    unsigned = SignedActionRequest(**values)
    device_signature = base64.urlsafe_b64encode(
        device_private.sign(canonical_action_bytes(unsigned))
    ).decode()
    with_device = unsigned.model_copy(update={"device_signature": device_signature})
    cloud_signature = base64.urlsafe_b64encode(
        cloud_private.sign(canonical_action_bytes(with_device))
    ).decode()
    signed = with_device.model_copy(update={"cloud_signature": cloud_signature})
    return signed, device_private.public_key(), cloud_private.public_key()


def test_expired_action_is_rejected():
    action, device_key, cloud_key = _signed_action()
    verifier = ActionVerifier(clock=lambda: NOW + timedelta(seconds=31))
    verifier.register_device("device_1", "device-key-1", device_key)
    verifier.register_cloud_key("cloud-key-1", cloud_key)

    assert verifier.consume(action, current_state_version=4, current_state_hash="sha256:state4").code == "expired"


def test_action_executes_at_most_once():
    action, device_key, cloud_key = _signed_action()
    verifier = ActionVerifier(clock=lambda: NOW)
    verifier.register_device("device_1", "device-key-1", device_key)
    verifier.register_cloud_key("cloud-key-1", cloud_key)

    first = verifier.consume(action, current_state_version=4, current_state_hash="sha256:state4")
    second = verifier.consume(action, current_state_version=4, current_state_hash="sha256:state4")

    assert first.status == "executed"
    assert second.code == "already_resolved"


def test_approval_for_old_pause_state_is_stale():
    action, device_key, cloud_key = _signed_action(kind=ActionKind.APPROVE)
    verifier = ActionVerifier(clock=lambda: NOW)
    verifier.register_device("device_1", "device-key-1", device_key)
    verifier.register_cloud_key("cloud-key-1", cloud_key)

    result = verifier.consume(
        action, current_state_version=5, current_state_hash="sha256:state5"
    )

    assert result.code == "stale_state"


def test_unknown_cloud_key_and_revoked_device_fail_closed():
    action, device_key, _cloud_key = _signed_action()
    verifier = ActionVerifier(clock=lambda: NOW)
    verifier.register_device("device_1", "device-key-1", device_key)
    assert verifier.consume(action, current_state_version=4, current_state_hash="sha256:state4").code == "unknown_cloud_key"

    _action, _device_key, cloud_key = _signed_action()
    verifier.register_cloud_key("cloud-key-1", cloud_key)
    verifier.revoke_device("device_1")
    assert verifier.consume(_action, current_state_version=4, current_state_hash="sha256:state4").code == "device_revoked"
