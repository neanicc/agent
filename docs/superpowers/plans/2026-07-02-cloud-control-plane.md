# Hosted Cloud Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a multi-tenant hosted service for authenticated event ingest, cursor replay, outbound daemon relay, expiring remote actions, artifacts, notifications, audit, and durable workflows.

**Architecture:** The local daemon opens an authenticated outbound WebSocket and uploads redacted events in batches. FastAPI persists tenant-scoped records in PostgreSQL before acknowledging them, uses an outbox for realtime delivery, stores large encrypted artifacts in object storage, and delegates long-running work to Temporal.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 async, PostgreSQL 16, Alembic, Temporal, S3-compatible object storage, OIDC/JWT, Ed25519/P-256, pytest, Testcontainers

---

### Task 1: Scaffold the service with strict settings

**Files:**
- Create: `services/control-api/pyproject.toml`
- Create: `services/control-api/src/loopguard_api/__init__.py`
- Create: `services/control-api/src/loopguard_api/settings.py`
- Create: `services/control-api/src/loopguard_api/app.py`
- Create: `services/control-api/tests/test_health.py`

- [ ] **Step 1: Write failing health/config tests**

```python
import pytest
from fastapi.testclient import TestClient

from loopguard_api.app import create_app
from loopguard_api.settings import Settings


def test_health_exposes_build_not_secrets():
    app = create_app(Settings.for_test())
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert "build_sha" in body
    assert "database_url" not in body


def test_production_rejects_default_signing_key():
    with pytest.raises(ValueError):
        Settings(environment="production", action_signing_key="development")
```

- [ ] **Step 2: Run tests and verify missing package**

Run: `cd services/control-api && python -m pytest -q tests/test_health.py`
Expected: FAIL because the service is not installed.

- [ ] **Step 3: Implement app factory and validated settings**

Use `pydantic-settings`. Production requires explicit database, OIDC issuer/audience, action
signing key reference, object-storage bucket, and Temporal endpoint. The app factory installs
request IDs, structured logging, exception mapping, and no permissive CORS.

- [ ] **Step 4: Run health tests**

Run: `cd services/control-api && python -m pytest -q tests/test_health.py`
Expected: PASS.

- [ ] **Step 5: Commit the service skeleton**

```bash
git add services/control-api
git commit -m "feat: scaffold hosted control api"
```

### Task 2: Add tenant-scoped database models and migrations

**Files:**
- Create: `services/control-api/src/loopguard_api/db.py`
- Create: `services/control-api/src/loopguard_api/models.py`
- Create: `services/control-api/alembic.ini`
- Create: `services/control-api/alembic/env.py`
- Create: `services/control-api/alembic/versions/0001_initial.py`
- Test: `services/control-api/tests/test_tenant_isolation.py`

- [ ] **Step 1: Write failing cross-tenant tests**

```python
def test_repository_query_is_tenant_scoped(db_session, tenant_a, tenant_b):
    repo = create_repo(db_session, tenant_id=tenant_a.id, name="private")
    assert list_repositories(db_session, tenant_id=tenant_a.id) == [repo]
    assert list_repositories(db_session, tenant_id=tenant_b.id) == []


def test_duplicate_event_is_idempotent(db_session, session_a):
    first = ingest_event(db_session, session_a, event_id="evt_1")
    second = ingest_event(db_session, session_a, event_id="evt_1")
    assert first.cursor == second.cursor
```

- [ ] **Step 2: Verify failure**

Run: `cd services/control-api && python -m pytest -q tests/test_tenant_isolation.py`
Expected: FAIL because models do not exist.

- [ ] **Step 3: Implement initial schema**

Create tables:

```text
tenants
users
memberships
devices
hosts
repositories
sessions
events
actions
action_deliveries
artifacts
audit_entries
relay_outbox
```

Every tenant-owned table has `tenant_id`, UUID primary key, creation timestamp, and appropriate
uniqueness. Events use unique `(tenant_id, event_id)` and monotonic per-session cursor. Repository
queries require an explicit `TenantContext`; repository methods without it are not exposed.

- [ ] **Step 4: Apply/revert migration and run isolation tests**

Run:

```bash
cd services/control-api
alembic upgrade head
python -m pytest -q tests/test_tenant_isolation.py
alembic downgrade base
alembic upgrade head
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit database foundation**

```bash
git add services/control-api
git commit -m "feat: add tenant-scoped control plane schema"
```

### Task 3: Implement OIDC authentication and authorization

**Files:**
- Create: `services/control-api/src/loopguard_api/auth.py`
- Create: `services/control-api/src/loopguard_api/authorization.py`
- Test: `services/control-api/tests/test_auth.py`

- [ ] **Step 1: Write failing issuer/audience/role tests**

```python
def test_rejects_wrong_audience(client, token_factory):
    response = client.get("/v1/me", headers={
        "Authorization": f"Bearer {token_factory(aud='wrong')}"
    })
    assert response.status_code == 401


def test_viewer_cannot_create_action(client, viewer_token, session_id):
    response = client.post(
        "/v1/actions",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"session_id": session_id, "kind": "interrupt"},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Verify failure**

Run: `cd services/control-api && python -m pytest -q tests/test_auth.py`
Expected: FAIL because authentication is absent.

- [ ] **Step 3: Implement verified principals and explicit permissions**

Validate JWT signature through cached JWKS, issuer, audience, expiry, and nonce where required.
Map external subject to membership. Define permissions:

```python
VIEW_SESSION = "session:view"
CONTROL_SESSION = "session:control"
MANAGE_POLICY = "policy:manage"
RUN_REPAIR = "repair:run"
PUBLISH_REPAIR = "repair:publish"
MANAGE_DEVICES = "device:manage"
```

Endpoints depend on `require_permission()` and never trust tenant IDs from request bodies.

- [ ] **Step 4: Run auth tests**

Run: `cd services/control-api && python -m pytest -q tests/test_auth.py`
Expected: PASS.

- [ ] **Step 5: Commit authentication and authorization**

```bash
git add services/control-api
git commit -m "feat: secure control api with oidc roles"
```

### Task 4: Pair hosts and establish outbound relay

**Files:**
- Create: `services/control-api/src/loopguard_api/pairing.py`
- Create: `services/control-api/src/loopguard_api/relay.py`
- Create: `services/control-api/src/loopguard_api/routes/hosts.py`
- Test: `services/control-api/tests/test_pairing_relay.py`
- Create: `loopguard/src/loopguard/control/cloud_relay.py`
- Test: `loopguard/tests/control/test_cloud_relay.py`

- [ ] **Step 1: Write failing one-time pairing and reconnect tests**

```python
def test_pairing_code_is_single_use(client, owner_token):
    code = create_pairing_code(client, owner_token)
    assert pair_host(client, code).status_code == 201
    assert pair_host(client, code).status_code == 409


def test_daemon_replays_after_last_ack(fake_cloud, local_store):
    relay = CloudRelay(local_store, fake_cloud, repo_id="r")
    fake_cloud.disconnect_after(cursor=4)
    run(relay.sync())
    assert fake_cloud.received_cursors == [1, 2, 3, 4, 5, 6]
```

- [ ] **Step 2: Verify failures**

Run:

```bash
cd services/control-api
python -m pytest -q tests/test_pairing_relay.py
cd ../../loopguard
python -m pytest -q tests/control/test_cloud_relay.py
```

Expected: FAIL because pairing and relay do not exist.

- [ ] **Step 3: Implement pairing and cursor protocol**

Pairing codes are random, hashed at rest, expire after five minutes, and are consumed atomically.
The host generates an Ed25519 keypair locally; only the public key is registered. Relay
authentication uses a short-lived host token plus signed challenge.

Protocol:

```json
{"type":"events","repo_id":"r","after_cursor":4,"events":[...]}
{"type":"ack","through_cursor":10}
```

Server persists all events and an outbox row in one transaction before ack. Client deletes
nothing locally after ack; it advances a relay cursor.

- [ ] **Step 4: Run pairing, duplicate, reconnect, and backpressure tests**

Run:

```bash
cd services/control-api && python -m pytest -q tests/test_pairing_relay.py
cd ../../loopguard && python -m pytest -q tests/control/test_cloud_relay.py
```

Expected: PASS.

- [ ] **Step 5: Commit secure outbound relay**

```bash
git add services/control-api loopguard/src/loopguard/control \
  loopguard/tests/control/test_cloud_relay.py
git commit -m "feat: pair hosts and relay durable events"
```

### Task 5: Add cursor replay and live subscriptions

**Files:**
- Create: `services/control-api/src/loopguard_api/routes/sessions.py`
- Create: `services/control-api/src/loopguard_api/subscriptions.py`
- Test: `services/control-api/tests/test_session_stream.py`

- [ ] **Step 1: Write failing replay-before-live tests**

```python
def test_websocket_replays_then_streams_without_gap(client, session, events):
    with client.websocket_connect(
        f"/v1/sessions/{session.id}/stream?after=2", headers=auth_headers()
    ) as ws:
        assert ws.receive_json()["cursor"] == 3
        publish_event(session, cursor=4)
        assert ws.receive_json()["cursor"] == 4
```

- [ ] **Step 2: Verify failure**

Run: `cd services/control-api && python -m pytest -q tests/test_session_stream.py`
Expected: FAIL because session streaming is absent.

- [ ] **Step 3: Implement replay and outbox notification**

Authorize before upgrade. In one flow: subscribe to session notification channel, query rows after
cursor, emit replay, then consume notifications and fill any cursor gaps from PostgreSQL. Send
heartbeats and close slow clients with a resumable last-cursor reason. Never rely on notification
delivery for durability.

- [ ] **Step 4: Run stream tests**

Run: `cd services/control-api && python -m pytest -q tests/test_session_stream.py`
Expected: PASS for reconnect, duplicate notification, gap, slow client, unauthorized tenant, and
session deletion.

- [ ] **Step 5: Commit replayable subscriptions**

```bash
git add services/control-api
git commit -m "feat: stream sessions with cursor replay"
```

### Task 6: Implement expiring signed actions

**Files:**
- Create: `services/control-api/src/loopguard_api/actions.py`
- Create: `services/control-api/src/loopguard_api/routes/actions.py`
- Test: `services/control-api/tests/test_actions.py`
- Create: `loopguard/src/loopguard/control/actions.py`
- Test: `loopguard/tests/control/test_actions.py`

- [ ] **Step 1: Write failing replay/expiry/state tests**

```python
def test_expired_action_is_rejected(action_service, clock):
    action = action_service.create(kind="interrupt", expires_in=30)
    clock.advance(seconds=31)
    assert action_service.consume(action).code == "expired"


def test_action_executes_at_most_once(action_service):
    action = action_service.create(kind="continue_once", expires_in=30)
    assert action_service.consume(action).status == "executed"
    assert action_service.consume(action).code == "already_resolved"


def test_approval_for_old_pause_state_is_stale(action_service):
    action = action_service.create(kind="approve", expected_state_version=4)
    assert action_service.consume(action, current_state_version=5).code == "stale_state"
```

- [ ] **Step 2: Verify failures**

Run:

```bash
cd services/control-api && python -m pytest -q tests/test_actions.py
cd ../../loopguard && python -m pytest -q tests/control/test_actions.py
```

Expected: FAIL because action services are absent.

- [ ] **Step 3: Implement signed capability actions**

Action fields:

```python
from loopguard.control.decisions import ActionRequest


class SignedActionRequest(ActionRequest):
    tenant_id: str
    requested_by: str
    signature: str
```

The cloud signs the canonical payload. The daemon verifies signature, tenant/host/session binding,
expiry, nonce, current state, and local policy. Resolution uses a unique action ID transaction and
creates immutable audit events on both sides.

- [ ] **Step 4: Run cloud/local action tests**

Run:

```bash
cd services/control-api && python -m pytest -q tests/test_actions.py
cd ../../loopguard && python -m pytest -q tests/control/test_actions.py
```

Expected: PASS.

- [ ] **Step 5: Commit secure remote actions**

```bash
git add services/control-api loopguard/src/loopguard/control \
  loopguard/tests/control/test_actions.py
git commit -m "feat: execute signed expiring remote actions"
```

### Task 7: Store encrypted artifacts and retention metadata

**Files:**
- Create: `services/control-api/src/loopguard_api/artifacts.py`
- Create: `services/control-api/src/loopguard_api/routes/artifacts.py`
- Test: `services/control-api/tests/test_artifacts.py`

- [ ] **Step 1: Write failing tenant/key/expiry tests**

```python
def test_artifact_download_is_tenant_scoped(client, tenant_a_artifact, tenant_b_token):
    response = client.get(
        f"/v1/artifacts/{tenant_a_artifact.id}",
        headers=bearer(tenant_b_token),
    )
    assert response.status_code == 404


def test_expired_artifact_is_deleted_from_object_store(retention_worker, artifact, clock):
    clock.advance(days=31)
    retention_worker.run_once()
    assert not retention_worker.objects.exists(artifact.object_key)
```

- [ ] **Step 2: Verify failure**

Run: `cd services/control-api && python -m pytest -q tests/test_artifacts.py`
Expected: FAIL because artifact storage is absent.

- [ ] **Step 3: Implement envelope-encrypted uploads**

Use presigned uploads only after authorization and declared size/content type. Store tenant-scoped
object keys, SHA-256, byte count, media type, encryption metadata, retention class, and expiry.
Complete upload only after server-side HEAD/hash validation. Downloads use short-lived signed URLs
and write audit entries.

- [ ] **Step 4: Run artifact tests**

Run: `cd services/control-api && python -m pytest -q tests/test_artifacts.py`
Expected: PASS.

- [ ] **Step 5: Commit artifact lifecycle**

```bash
git add services/control-api
git commit -m "feat: store encrypted evidence artifacts"
```

### Task 8: Add Temporal workflows, notifications, and immutable audit

**Files:**
- Create: `services/control-api/src/loopguard_api/workflows.py`
- Create: `services/control-api/src/loopguard_api/notifications.py`
- Create: `services/control-api/src/loopguard_api/audit.py`
- Test: `services/control-api/tests/test_workflows.py`
- Test: `services/control-api/tests/test_audit.py`

- [ ] **Step 1: Write failing retry/idempotency tests**

```python
def test_notification_retry_does_not_duplicate_delivery(temporal_env, notification_service):
    notification_service.fail_first_attempt = True
    result = temporal_env.run_workflow("notify-action", action_id="a1")
    assert result.status == "delivered"
    assert notification_service.accepted_ids == ["a1"]


def test_audit_entry_cannot_be_updated(db_session, audit_entry):
    with pytest.raises(ImmutableRecordError):
        update_audit_entry(db_session, audit_entry.id, {"result": "changed"})
```

- [ ] **Step 2: Verify failure**

Run: `cd services/control-api && python -m pytest -q tests/test_workflows.py tests/test_audit.py`
Expected: FAIL because workflows/audit are absent.

- [ ] **Step 3: Implement deterministic workflows and append-only audit**

Initial workflows:

- Deliver action notification.
- Expire stale action.
- Delete expired artifact.
- Start/cancel verification or repair child workflow.

Use workflow IDs derived from domain IDs, activity idempotency keys, bounded retry policies, and no
secrets in workflow history. Audit records include actor, action, target, request ID, before/after
state hashes, result, IP/device metadata, and timestamp. Database privileges deny update/delete on
audit rows to the application role.

- [ ] **Step 4: Run service tests**

Run: `cd services/control-api && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit workflows and audit**

```bash
git add services/control-api
git commit -m "feat: add durable workflows and immutable audit"
```

### Task 9: Expose preference, cost, device, and audit control APIs

**Files:**
- Create: `services/control-api/alembic/versions/0002_control_queries.py`
- Create: `services/control-api/src/loopguard_api/routes/preferences.py`
- Create: `services/control-api/src/loopguard_api/routes/costs.py`
- Create: `services/control-api/src/loopguard_api/routes/devices.py`
- Create: `services/control-api/src/loopguard_api/routes/audit.py`
- Create: `services/control-api/src/loopguard_api/device_pairing.py`
- Test: `services/control-api/tests/test_control_queries.py`
- Modify: `services/control-api/src/loopguard_api/app.py`

- [ ] **Step 1: Write failing tenant, policy, and aggregation tests**

```python
def test_preference_profile_preserves_managed_safety_rules(client, admin_token):
    response = client.put(
        "/v1/preferences",
        headers=bearer(admin_token),
        json={"rules": [{"id": "wcag-contrast", "severity": "inform"}]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "managed_rule_weakened"


def test_cost_summary_separates_observed_categories(client, viewer_token, usage_records):
    response = client.get("/v1/costs?window=30d", headers=bearer(viewer_token))
    assert response.status_code == 200
    assert response.json()["observed"] == {
        "agent": "1.20",
        "judge": "0.04",
        "verification": "0.00",
        "critic": "0.01",
        "repair": "0.30",
    }
    assert response.json()["estimated_avoided_cost"] is None


def test_audit_and_devices_are_tenant_scoped(
    client, tenant_a_token, tenant_b_device, tenant_b_audit
):
    assert client.get("/v1/devices", headers=bearer(tenant_a_token)).json()["items"] == []
    assert client.get("/v1/audit", headers=bearer(tenant_a_token)).json()["items"] == []


def test_device_pairing_challenge_is_single_use(client, owner_token, device_key):
    started = client.post("/v1/devices/pairing/start", headers=bearer(owner_token)).json()
    payload = {
        "pairing_id": started["pairing_id"],
        "public_key_alg": device_key.algorithm,
        "public_key": device_key.public_key,
        "signature": device_key.sign(started["challenge"]),
        "name": "Alice iPhone",
    }
    assert client.post(
        "/v1/devices/pairing/complete", headers=bearer(owner_token), json=payload
    ).status_code == 201
    assert client.post(
        "/v1/devices/pairing/complete", headers=bearer(owner_token), json=payload
    ).status_code == 409
```

- [ ] **Step 2: Verify the client-required APIs are absent**

Run: `cd services/control-api && python -m pytest -q tests/test_control_queries.py`
Expected: FAIL with 404 responses for `/v1/preferences`, `/v1/costs`, `/v1/devices`,
`/v1/devices/pairing/start`, `/v1/devices/pairing/complete`, and `/v1/audit`.

- [ ] **Step 3: Implement versioned tenant-scoped query services**

Migration `0002_control_queries.py` creates `preference_profiles` and `usage_records`.
`preference_profiles` stores profile version, source manifest hash, rules JSON, and updater.
`usage_records` uses unique `(tenant_id, usage_id)`, observed category, amount, currency, provider,
session, and timestamp.

Routes:

```python
router = APIRouter(prefix="/v1")


@router.get("/preferences", response_model=PreferenceProfileResponse)
async def read_preferences(ctx: TenantContext = Depends(require_viewer)): ...


@router.put("/preferences", response_model=PreferenceProfileResponse)
async def write_preferences(
    request: PreferenceProfileUpdate,
    ctx: TenantContext = Depends(require_policy_manager),
): ...


@router.get("/costs", response_model=CostSummary)
async def read_costs(
    window: Literal["24h", "7d", "30d", "90d"],
    ctx: TenantContext = Depends(require_viewer),
): ...


@router.get("/devices", response_model=DevicePage)
async def list_devices(ctx: TenantContext = Depends(require_viewer)): ...


@router.post("/devices/pairing/start", response_model=DevicePairingChallenge)
async def start_device_pairing(
    ctx: TenantContext = Depends(require_device_manager),
): ...


@router.post(
    "/devices/pairing/complete",
    response_model=DeviceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def complete_device_pairing(
    request: DevicePairingCompletion,
    ctx: TenantContext = Depends(require_device_manager),
): ...


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    device_id: UUID,
    ctx: TenantContext = Depends(require_device_manager),
): ...


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: TenantContext = Depends(require_viewer),
): ...
```

The costs endpoint labels every number as observed or estimated and never invents avoided cost.
Device pairing challenges are random, hashed at rest, bound to the authenticated user/tenant,
expire after five minutes, and are consumed atomically after proof-of-possession. The device record
stores an explicit allowlisted algorithm: P-256 for Secure Enclave-backed Apple keys or Ed25519 for
Keychain/software-backed clients. Reject unknown algorithms, malformed encodings, and algorithm
confusion. Revoking a device invalidates future device-signed actions without deleting audit
history. Audit pagination uses immutable `(created_at, id)` cursors. Register all routers in
`app.py`.

- [ ] **Step 4: Run query, authorization, migration, and full service tests**

Run:

```bash
cd services/control-api
alembic upgrade head
python -m pytest -q tests/test_control_queries.py tests/test_auth.py
python -m pytest -q
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit client-required control APIs**

```bash
git add services/control-api
git commit -m "feat: expose preference cost device and audit APIs"
```

## Completion gate

Run:

```bash
cd services/control-api
alembic upgrade head
python -m pytest -q
ruff check src tests
```

Expected: all commands exit 0; duplicate events/actions are idempotent; cross-tenant access tests
fail closed; and disconnect/reconnect resumes from the last persisted cursor.
