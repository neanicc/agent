from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from loopguard_api.app import create_app
from loopguard_api.authorization import Permission, Principal
from loopguard_api.client_queries import ControlQueryService
from loopguard_api.device_pairing import DevicePairingService
from loopguard_api.settings import Settings


NOW = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
TENANT_A, TENANT_B = uuid.uuid4(), uuid.uuid4()
USER_A = uuid.uuid4()


class StaticAuth:
    async def authenticate(self, token: str) -> Principal:
        tenant_id = TENANT_B if token == "tenant-b" else TENANT_A
        role = "viewer" if token == "viewer" else "admin"
        permissions = (
            frozenset({Permission.VIEW_SESSION}) if role == "viewer" else frozenset(Permission)
        )
        return Principal(tenant_id, USER_A, f"subject-{role}", role, permissions)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def client_and_services():
    queries = ControlQueryService(clock=lambda: NOW)
    devices = DevicePairingService(clock=lambda: NOW)
    app = create_app(
        Settings.for_test(),
        auth_service=StaticAuth(),  # type: ignore[arg-type]
        control_queries=queries,
        device_pairing_service=devices,
    )
    return TestClient(app), queries, devices


def test_preference_profile_preserves_managed_safety_rules():
    client, _queries, _devices = client_and_services()
    response = client.put(
        "/v1/preferences",
        headers=bearer("admin"),
        json={"rules": [{"id": "wcag-contrast", "severity": "inform"}]},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "LGAPI-MANAGED-RULE-WEAKENED"


def test_cost_summary_separates_observed_categories():
    client, queries, _devices = client_and_services()
    for category, amount in {
        "agent": "1.20",
        "judge": "0.04",
        "verification": "0.00",
        "critic": "0.01",
        "repair": "0.30",
    }.items():
        queries.record_usage(
            tenant_id=TENANT_A,
            usage_id=f"usage-{category}",
            category=category,
            amount=Decimal(amount),
            currency="USD",
            provider="test",
            observed_at=NOW - timedelta(days=1),
        )

    response = client.get("/v1/costs?window=30d", headers=bearer("viewer"))

    assert response.status_code == 200
    assert response.json()["observed"] == {
        "agent": "1.20",
        "judge": "0.04",
        "verification": "0.00",
        "critic": "0.01",
        "repair": "0.30",
    }
    assert response.json()["estimated_avoided_cost"] is None


def test_audit_and_devices_are_tenant_scoped():
    client, queries, devices = client_and_services()
    devices.seed_device(
        tenant_id=TENANT_B,
        user_id=uuid.uuid4(),
        name="other tenant",
        algorithm="Ed25519",
        public_key="key",
    )
    queries.add_audit(
        tenant_id=TENANT_B,
        action="private",
        target_kind="device",
        target_id="private",
    )

    assert client.get("/v1/devices", headers=bearer("admin")).json()["items"] == []
    assert client.get("/v1/audit", headers=bearer("admin")).json()["items"] == []


def test_device_pairing_challenge_is_single_use():
    client, _queries, _devices = client_and_services()
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.urlsafe_b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    started = client.post("/v1/devices/pairing/start", headers=bearer("admin")).json()
    payload = {
        "pairing_id": started["pairing_id"],
        "public_key_alg": "Ed25519",
        "public_key": public_key,
        "signature": base64.urlsafe_b64encode(
            private_key.sign(base64.urlsafe_b64decode(started["challenge"]))
        ).decode(),
        "name": "Alice iPhone",
    }

    assert (
        client.post(
            "/v1/devices/pairing/complete", headers=bearer("admin"), json=payload
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/v1/devices/pairing/complete", headers=bearer("admin"), json=payload
        ).status_code
        == 409
    )


def test_push_destination_registration_is_tenant_scoped_and_not_exposed():
    client, _queries, devices = client_and_services()
    device = devices.seed_device(
        tenant_id=TENANT_A,
        user_id=USER_A,
        name="Alice iPhone",
        algorithm="Ed25519",
        public_key="key",
    )
    token = "ab" * 32

    response = client.put(
        f"/v1/devices/{device.id}/push-token",
        headers=bearer("admin"),
        json={"token": token, "environment": "sandbox"},
    )

    assert response.status_code == 200
    assert devices.push_destination(TENANT_A, device.id) == ("sandbox", token)
    listed = client.get("/v1/devices", headers=bearer("admin")).json()["items"]
    assert "push_token" not in listed[0]
    assert (
        client.put(
            f"/v1/devices/{device.id}/push-token",
            headers=bearer("tenant-b"),
            json={"token": token, "environment": "sandbox"},
        ).status_code
        == 404
    )


def test_effective_capabilities_fail_closed_for_stale_host():
    client, queries, _devices = client_and_services()
    host_id = queries.add_host(
        tenant_id=TENANT_A,
        name="stale",
        observed_at=NOW - timedelta(minutes=10),
        ttl_seconds=60,
        adapter_version="1.0.0",
        repository_bound=True,
    )

    body = client.get(f"/v1/capabilities?host_id={host_id}", headers=bearer("viewer")).json()

    assert body["status"] == "degraded"
    assert body["features"]["remote_actions"]["available"] is False
    assert body["features"]["remote_actions"]["reason"] == "host_health_stale"
