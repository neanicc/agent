from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from loopguard_api.app import create_app
from loopguard_api.authorization import Permission, Principal
from loopguard_api.client_queries import ControlQueryService
from loopguard_api.settings import Settings


TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
USER_A = uuid.uuid4()
USER_B = uuid.uuid4()
NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


class StaticAuth:
    async def authenticate(self, token: str) -> Principal:
        tenant_id, user_id = (
            (TENANT_B, USER_B) if token == "tenant-b" else (TENANT_A, USER_A)
        )
        return Principal(
            tenant_id=tenant_id,
            user_id=user_id,
            subject=f"subject-{tenant_id}",
            role="admin",
            permissions=frozenset(Permission),
        )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def client_with_tenant_a_session() -> tuple[TestClient, uuid.UUID, uuid.UUID]:
    queries = ControlQueryService(clock=lambda: NOW)
    host_id = queries.add_host(
        tenant_id=TENANT_A,
        name="tenant-a-host",
        observed_at=NOW,
        ttl_seconds=60,
        adapter_version="1.0.0",
        repository_bound=True,
    )
    session_id = queries.add_resource(
        "sessions",
        tenant_id=TENANT_A,
        values={"status": "paused"},
    )
    app = create_app(
        Settings.for_test(),
        auth_service=StaticAuth(),  # type: ignore[arg-type]
        control_queries=queries,
    )
    return TestClient(app), session_id, host_id


def challenge_payload(session_id: uuid.UUID, host_id: uuid.UUID) -> dict[str, object]:
    return {
        "target": {
            "kind": "session",
            "target_id": str(session_id),
            "host_id": str(host_id),
        },
        "device_id": str(uuid.uuid4()),
        "kind": "interrupt",
        "expected_state_version": 1,
        "expected_state_hash": "sha256:state",
    }


def test_cross_tenant_session_id_cannot_authorize_action() -> None:
    client, session_id, host_id = client_with_tenant_a_session()

    response = client.post(
        "/v1/actions/challenge",
        headers=bearer("tenant-b"),
        json=challenge_payload(session_id, host_id),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "LGAPI-NOT-FOUND"


def test_action_requires_target_and_host_from_same_tenant() -> None:
    client, session_id, host_id = client_with_tenant_a_session()
    payload = challenge_payload(session_id, host_id)
    payload["target"] = {
        **payload["target"],  # type: ignore[dict-item]
        "host_id": str(uuid.uuid4()),
    }

    response = client.post(
        "/v1/actions/challenge",
        headers=bearer("tenant-a"),
        json=payload,
    )

    assert response.status_code == 404


def test_action_kind_cannot_be_retargeted_to_an_unrelated_object() -> None:
    client, session_id, host_id = client_with_tenant_a_session()
    payload = challenge_payload(session_id, host_id)
    payload["target"] = {
        "kind": "repository",
        "target_id": str(session_id),
        "host_id": str(host_id),
    }

    response = client.post(
        "/v1/actions/challenge",
        headers=bearer("tenant-a"),
        json=payload,
    )

    assert response.status_code == 403


def test_cross_tenant_bearer_cannot_accept_another_tenants_signed_action() -> None:
    client, session_id, host_id = client_with_tenant_a_session()
    service = client.app.state.action_service
    key = Ed25519PrivateKey.generate()
    device_id = uuid.uuid4()
    service.register_device(
        tenant_id=TENANT_A,
        user_id=USER_A,
        device_id=device_id,
        key_id="device-a",
        algorithm="Ed25519",
        public_key=key.public_key(),
    )
    challenge = service.create_challenge(
        principal=Principal(
            TENANT_A,
            USER_A,
            "subject-a",
            "admin",
            frozenset(Permission),
        ),
        requested_by_device_id=device_id,
        target_kind="session",
        target_id=str(session_id),
        host_id=str(host_id),
        kind="interrupt",
        parameters={},
        expected_state_version=1,
        expected_state_hash="sha256:state",
        expires_in=30,
    )

    response = client.post(
        "/v1/actions",
        headers=bearer("tenant-b"),
        json={
            "action_id": challenge.action_id,
            "device_id": str(device_id),
            "device_key_id": "device-a",
            "device_algorithm": "Ed25519",
            "device_signature": base64.urlsafe_b64encode(
                key.sign(challenge.canonical_bytes)
            ).decode(),
        },
    )

    assert response.status_code == 404
    assert service.read(challenge.action_id).state == "reviewed"
