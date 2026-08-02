from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from loopguard_api.app import create_app
from loopguard_api.authorization import Permission, Principal
from loopguard_api.repair_workflow import RepairWorkflowService
from loopguard_api.settings import Settings


TENANT_A, TENANT_B = uuid.uuid4(), uuid.uuid4()
USER = uuid.uuid4()
NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


class StaticAuth:
    async def authenticate(self, token: str) -> Principal:
        tenant = TENANT_B if token == "tenant-b" else TENANT_A
        is_admin = token == "admin-a"
        return Principal(
            tenant,
            USER,
            "subject",
            "admin" if is_admin else "viewer",
            (frozenset(Permission) if is_admin else frozenset({Permission.VIEW_SESSION})),
        )


def _headers(token: str = "tenant-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed(service: RepairWorkflowService, tenant: uuid.UUID, index: int) -> uuid.UUID:
    return service.seed_detail(
        tenant_id=tenant,
        repository_id=uuid.uuid4(),
        failure_fingerprint=f"{index:x}".rjust(64, "0"),
        state="awaiting_publication",
        reproduction={
            "status": "reproduced",
            "artifact_id": "artifact-reproduction",
            "attempts": 1,
        },
        candidates=[
            {
                "id": f"candidate-{index}",
                "strategy": "normalize_ingestion_boundary",
                "changed_files": ["src/ingest.py"],
                "changed_lines": 8,
                "patch_artifact_id": "artifact-patch",
                "evaluation": {
                    "replay": "passed",
                    "regression": "passed",
                    "security": "passed",
                    "contract_breaking": False,
                    "artifact_ids": ["artifact-evaluation"],
                },
            }
        ],
        ranking={
            "winning_candidate_id": f"candidate-{index}",
            "reason": "smallest verified compatible patch",
        },
        rollback="Revert the repair commit.",
        publication={"status": "waiting_for_approval"},
    )


def _client() -> tuple[TestClient, RepairWorkflowService]:
    service = RepairWorkflowService(clock=lambda: NOW, workflow_registered=True)
    app = create_app(
        Settings.for_test(),
        auth_service=StaticAuth(),  # type: ignore[arg-type]
        repair_workflow_service=service,
    )
    return TestClient(app), service


def test_repair_detail_is_tenant_scoped_and_redacted() -> None:
    client, service = _client()
    repair_id = _seed(service, TENANT_B, 1)

    assert client.get(f"/v1/repairs/{repair_id}", headers=_headers()).status_code == 404
    response = client.get(
        f"/v1/repairs/{repair_id}",
        headers=_headers("tenant-b"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reproduction"]["status"] == "reproduced"
    assert len(body["candidates"]) == 1
    assert body["capability"] == {
        "available": True,
        "reason": "workflow_registered",
    }
    assert "raw_fixture" not in body
    assert "provider_token" not in str(body)
    assert "stack_trace" not in str(body)


def test_repair_list_uses_bounded_immutable_cursor() -> None:
    client, service = _client()
    for index in range(3):
        _seed(service, TENANT_A, index + 1)

    first = client.get("/v1/repairs?limit=2", headers=_headers()).json()
    second = client.get(
        f"/v1/repairs?limit=2&page_cursor={first['next_cursor']}",
        headers=_headers(),
    ).json()

    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert {item["id"] for item in first["items"]}.isdisjoint(
        item["id"] for item in second["items"]
    )


def test_invalid_cursor_and_limit_fail_as_typed_requests() -> None:
    client, _service = _client()

    assert (
        client.get("/v1/repairs?limit=201", headers=_headers()).json()["code"]
        == "LGAPI-REQUEST-INVALID"
    )
    assert (
        client.get("/v1/repairs?page_cursor=forged", headers=_headers()).json()["code"]
        == "LGAPI-REQUEST-INVALID"
    )


def test_publish_repair_requires_a_signed_expected_state_action() -> None:
    client, workflow = _client()
    repair_id = _seed(workflow, TENANT_A, 9)
    repair = workflow.read(TENANT_A, repair_id)
    assert repair is not None
    device_id = uuid.uuid4()
    device_key = Ed25519PrivateKey.generate()
    client.app.state.action_service.register_device(
        tenant_id=TENANT_A,
        user_id=USER,
        device_id=device_id,
        key_id="device-key-1",
        algorithm="Ed25519",
        public_key=device_key.public_key(),
    )

    challenge = client.post(
        "/v1/actions/challenge",
        headers=_headers("admin-a"),
        json={
            "target": {
                "kind": "repair",
                "target_id": str(repair_id),
                "host_id": "repair-worker",
            },
            "device_id": str(device_id),
            "kind": "publish_repair",
            "parameters": {},
            "expected_state_version": repair.state_version,
            "expected_state_hash": repair.state_hash,
            "expires_in": 30,
        },
    )
    assert challenge.status_code == 201
    canonical = base64.urlsafe_b64decode(challenge.json()["canonical_payload"])
    signature = base64.urlsafe_b64encode(device_key.sign(canonical)).decode()

    accepted = client.post(
        "/v1/actions",
        headers=_headers("admin-a"),
        json={
            "action_id": challenge.json()["action_id"],
            "device_id": str(device_id),
            "device_key_id": "device-key-1",
            "device_algorithm": "Ed25519",
            "device_signature": signature,
        },
    )

    assert accepted.status_code == 202
    assert accepted.json()["state"] == "executed"
    updated = workflow.read(TENANT_A, repair_id)
    assert updated is not None
    assert updated.approved_action_id == challenge.json()["action_id"]
