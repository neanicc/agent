# Production Security and Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LoopGuard safe and operable as local software and a multi-tenant hosted service with tested isolation, recovery, observability, release provenance, retention, and incident response.

**Architecture:** Security controls are enforced at local ingestion, cloud authorization, action execution, sandbox boundaries, and release pipelines. Operational readiness is proven through automated abuse, load, chaos, backup/restore, and migration exercises before general availability.

**Tech Stack:** OpenTelemetry, PostgreSQL, Temporal, S3-compatible storage, OIDC, Sigstore, SBOM, container scanning, Terraform, Kubernetes, pytest, k6

---

### Task 1: Create and enforce the production threat model

**Files:**
- Create: `docs/security/threat-model.md`
- Create: `docs/security/trust-boundaries.md`
- Create: `services/control-api/tests/security/test_abuse_paths.py`
- Create: `loopguard/tests/security/test_local_abuse_paths.py`

- [ ] **Step 1: Encode critical abuse paths as failing tests**

```python
def test_cross_tenant_session_id_cannot_authorize_action(client, tenant_a_session, tenant_b_admin):
    response = client.post(
        "/v1/actions",
        headers=bearer(tenant_b_admin),
        json={"session_id": str(tenant_a_session.id), "kind": "interrupt"},
    )
    assert response.status_code in {403, 404}


def test_hook_payload_cannot_escape_local_state_directory(local_ingest, tmp_path):
    result = local_ingest({"artifact_path": "../../.ssh/id_ed25519"})
    assert result.code == "invalid_path"
```

- [ ] **Step 2: Run tests and record current failures**

Run:

```bash
cd services/control-api && python -m pytest -q tests/security
cd ../../loopguard && python -m pytest -q tests/security
```

Expected: FAIL until every documented critical abuse path has a control.

- [ ] **Step 3: Document assets, actors, boundaries, and mitigations**

Cover source code, transcripts, credentials, device keys, actions, artifacts, repair sandboxes,
GitHub installations, tenant data, billing, and audit. Actors include malicious repository,
compromised dependency, tenant user, stolen device, malicious hook, compromised model output, and
cloud operator. Every critical/high abuse path links to a test, owner, and mitigation.

- [ ] **Step 4: Implement controls and rerun abuse tests**

Expected: all security tests PASS; no critical/high item remains without an automated control or
documented accepted risk approved by the owner.

- [ ] **Step 5: Commit the threat model**

```bash
git add docs/security services/control-api/tests/security loopguard/tests/security \
  services/control-api/src loopguard/src
git commit -m "security: enforce loopguard trust boundaries"
```

### Task 2: Harden local state, secrets, and update behavior

**Files:**
- Create: `loopguard/src/loopguard/security/permissions.py`
- Create: `loopguard/src/loopguard/security/keys.py`
- Create: `loopguard/src/loopguard/security/update.py`
- Test: `loopguard/tests/security/test_permissions.py`
- Test: `loopguard/tests/security/test_update.py`

- [ ] **Step 1: Write failing permission and signature tests**

```python
def test_local_state_rejects_group_readable_secret_file(tmp_path):
    path = tmp_path / "host.key"
    path.write_text("secret")
    path.chmod(0o640)
    assert validate_private_file(path).code == "unsafe_permissions"


def test_update_manifest_requires_trusted_signature(update_verifier):
    assert update_verifier.verify(tampered_manifest()).code == "invalid_signature"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/security/test_permissions.py tests/security/test_update.py`
Expected: FAIL because local security services are absent.

- [ ] **Step 3: Implement platform key storage and signed updates**

Store device/host private keys in Keychain, DPAPI, or libsecret; use a 0600 encrypted-file fallback
only with an explicit warning. Validate socket/state ownership and permissions at daemon startup.
Update manifests include version, artifact digest, minimum database schema, release channel, and
signature. Never execute an unsigned downloaded binary.

- [ ] **Step 4: Run local security tests**

Run: `cd loopguard && python -m pytest -q tests/security`
Expected: PASS.

- [ ] **Step 5: Commit local hardening**

```bash
git add loopguard/src/loopguard/security loopguard/tests/security
git commit -m "security: harden local keys state and updates"
```

### Task 3: Add retention, export, and deletion workflows

**Files:**
- Create: `services/control-api/src/loopguard_api/retention.py`
- Create: `services/control-api/src/loopguard_api/privacy.py`
- Create: `services/control-api/tests/test_privacy_workflows.py`
- Create: `docs/privacy/data-inventory.md`

- [ ] **Step 1: Write failing deletion-completeness tests**

```python
def test_tenant_deletion_removes_rows_objects_and_search_indexes(deletion_workflow, tenant):
    seed_all_tenant_data(tenant)
    result = deletion_workflow.run(tenant.id)
    assert result.status == "completed"
    assert count_tenant_rows(tenant.id) == 0
    assert list_tenant_objects(tenant.id) == []


def test_audit_retains_tombstone_without_sensitive_payload(deletion_workflow, tenant):
    deletion_workflow.run(tenant.id)
    tombstone = read_deletion_tombstone(tenant.id)
    assert tombstone.tenant_id_hash
    assert not tombstone.payload
```

- [ ] **Step 2: Verify failure**

Run: `cd services/control-api && python -m pytest -q tests/test_privacy_workflows.py`
Expected: FAIL because privacy workflows are absent.

- [ ] **Step 3: Implement data inventory and durable workflows**

Inventory every field/object, purpose, classification, source, processor, default retention, and
deletion behavior. Implement export and deletion across PostgreSQL, object storage, Temporal search
attributes, notification tokens, and derived metrics. Deletion is idempotent, resumable, and
audited without retaining deleted content.

- [ ] **Step 4: Run privacy tests**

Run: `cd services/control-api && python -m pytest -q tests/test_privacy_workflows.py`
Expected: PASS.

- [ ] **Step 5: Commit privacy controls**

```bash
git add services/control-api docs/privacy
git commit -m "feat: add retention export and deletion workflows"
```

### Task 4: Instrument end-to-end observability and SLOs

**Files:**
- Create: `loopguard/src/loopguard/telemetry.py`
- Create: `services/control-api/src/loopguard_api/telemetry.py`
- Create: `docs/operations/slos.md`
- Create: `docs/operations/dashboards.md`
- Test: `services/control-api/tests/test_telemetry.py`
- Test: `loopguard/tests/test_telemetry.py`

- [ ] **Step 1: Write failing secret-safe trace tests**

```python
def test_event_span_contains_ids_not_payload(telemetry, control_event):
    span = telemetry.capture(lambda: telemetry.record_event(control_event))
    assert span.attributes["loopguard.event_id"] == control_event.event_id
    assert "payload" not in span.attributes
    assert "tool_args" not in span.attributes
```

- [ ] **Step 2: Verify failure**

Run:

```bash
cd loopguard && python -m pytest -q tests/test_telemetry.py
cd ../services/control-api && python -m pytest -q tests/test_telemetry.py
```

Expected: FAIL because instrumentation is missing.

- [ ] **Step 3: Implement traces, metrics, logs, and SLO definitions**

Propagate request/event/session/workflow IDs. Never put prompts, source, secrets, or raw tool output
in metric labels or span attributes. Define SLOs for local event durability, cloud ingest
availability, relay latency, action resolution, stream replay, verification completion, and repair
workflow availability. Define burn-rate alerts and ownership.

- [ ] **Step 4: Run telemetry tests and local collector smoke**

Expected: tests PASS and an OpenTelemetry collector receives spans without sensitive payloads.

- [ ] **Step 5: Commit observability**

```bash
git add loopguard/src/loopguard/telemetry.py loopguard/tests/test_telemetry.py \
  services/control-api/src/loopguard_api/telemetry.py \
  services/control-api/tests/test_telemetry.py docs/operations
git commit -m "feat: instrument production slos safely"
```

### Task 5: Add load, backpressure, and chaos testing

**Files:**
- Create: `services/control-api/load/k6-ingest.js`
- Create: `services/control-api/load/k6-stream.js`
- Create: `services/control-api/tests/chaos/test_relay_recovery.py`
- Create: `services/control-api/tests/chaos/test_workflow_recovery.py`
- Create: `docs/operations/capacity.md`

- [ ] **Step 1: Define failing capacity assertions**

```javascript
export const options = {
  thresholds: {
    http_req_failed: ["rate<0.001"],
    http_req_duration: ["p(95)<250", "p(99)<750"],
  },
};
```

Chaos tests must kill API/worker/database connections after persistence but before response and
assert idempotent recovery.

- [ ] **Step 2: Run baseline load/chaos tests**

Run:

```bash
cd services/control-api
k6 run load/k6-ingest.js
python -m pytest -q tests/chaos
```

Expected: thresholds initially fail or capacity is undocumented.

- [ ] **Step 3: Implement explicit limits and backpressure**

Set per-tenant batch, byte, connection, action, artifact, and workflow limits. Reject overload with
retryable status and jitter guidance; never accept and silently drop. Size database pools,
statement timeouts, outbox batches, WebSocket queues, and Temporal workers from measured load.

- [ ] **Step 4: Repeat until capacity thresholds pass**

Record tested hardware, dataset, concurrency, throughput, latency, saturation point, and next
scaling trigger in `capacity.md`.

- [ ] **Step 5: Commit load and chaos coverage**

```bash
git add services/control-api/load services/control-api/tests/chaos \
  services/control-api/src docs/operations/capacity.md
git commit -m "test: prove control plane load and recovery"
```

### Task 6: Prove backup, restore, and migration safety

**Files:**
- Create: `services/control-api/scripts/backup.sh`
- Create: `services/control-api/scripts/restore.sh`
- Create: `services/control-api/scripts/rehearse_migration.sh`
- Create: `services/control-api/tests/operations/test_restore.py`
- Create: `docs/operations/disaster-recovery.md`

- [ ] **Step 1: Write failing restored-integrity tests**

```python
def test_restore_preserves_event_cursor_and_artifact_hash(restored_environment, source_snapshot):
    restored = restored_environment.restore(source_snapshot)
    assert restored.max_event_cursor == source_snapshot.max_event_cursor
    assert restored.artifact_hashes == source_snapshot.artifact_hashes
    assert restored.action_resolutions == source_snapshot.action_resolutions
```

- [ ] **Step 2: Run restore test**

Run: `cd services/control-api && python -m pytest -q tests/operations/test_restore.py`
Expected: FAIL until backup/restore tooling exists.

- [ ] **Step 3: Implement encrypted backup and expand/contract migration rehearsal**

Back up PostgreSQL and object manifests with encryption and checksums. Restore into an isolated
environment and run integrity queries. Migrations use expand/backfill/read-switch/contract; the
rehearsal runs old and new application versions against the transition schema and measures lock
time.

- [ ] **Step 4: Run restore and migration rehearsal**

Run:

```bash
cd services/control-api
python -m pytest -q tests/operations/test_restore.py
scripts/rehearse_migration.sh
```

Expected: PASS within documented RPO/RTO and lock budgets.

- [ ] **Step 5: Commit recovery tooling**

```bash
git add services/control-api/scripts services/control-api/tests/operations \
  docs/operations/disaster-recovery.md
git commit -m "ops: verify backup restore and migrations"
```

### Task 7: Secure build, dependency, and release provenance

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `.github/workflows/security.yml`
- Create: `scripts/verify_release.sh`
- Create: `docs/operations/release-process.md`

- [ ] **Step 1: Add failing release verification**

`scripts/verify_release.sh` must fail when any artifact lacks checksum, SBOM, provenance
attestation, or signature.

- [ ] **Step 2: Run against an unsigned local build**

Run: `scripts/verify_release.sh dist/`
Expected: FAIL with a list of missing release evidence.

- [ ] **Step 3: Implement reproducible CI release**

CI uses pinned actions by commit, least-privilege permissions, isolated build jobs, dependency
review, secret scanning, SAST, container scanning, SBOM generation, Sigstore signing, provenance
attestation, and environment approval for promotion. Provider credentials are never available to
jobs that execute untrusted repository code.

- [ ] **Step 4: Build a release candidate and verify it**

Expected: `scripts/verify_release.sh dist/` exits 0 and validates every published artifact.

- [ ] **Step 5: Commit release security**

```bash
git add .github/workflows scripts/verify_release.sh docs/operations/release-process.md
git commit -m "ci: sign and attest loopguard releases"
```

### Task 8: Add metering, billing reconciliation, and quota safety

**Files:**
- Create: `services/control-api/src/loopguard_api/metering.py`
- Create: `services/control-api/src/loopguard_api/quotas.py`
- Create: `services/control-api/tests/test_metering.py`
- Create: `docs/operations/metering.md`

- [ ] **Step 1: Write failing idempotency and reconciliation tests**

```python
def test_usage_event_is_idempotent(meter):
    meter.record("usage_1", tenant_id="t", units=10)
    meter.record("usage_1", tenant_id="t", units=10)
    assert meter.total("t") == 10


def test_invoice_reconciles_to_immutable_usage_ledger(meter, invoice):
    assert meter.reconcile(invoice).difference == 0
```

- [ ] **Step 2: Verify failure**

Run: `cd services/control-api && python -m pytest -q tests/test_metering.py`
Expected: FAIL because metering is absent.

- [ ] **Step 3: Implement append-only usage and preflight quotas**

Meter hosted storage, retained events, managed compute, optional judge/critic spend, browser
minutes where hosted, and repair workers. Keep provider cost and LoopGuard charges distinct.
Preflight quota before expensive work and reserve/release units atomically. Never terminate local
guarding because hosted quota is exhausted.

- [ ] **Step 4: Run metering tests and reconciliation fixture**

Run: `cd services/control-api && python -m pytest -q tests/test_metering.py`
Expected: PASS.

- [ ] **Step 5: Commit metering controls**

```bash
git add services/control-api/src/loopguard_api services/control-api/tests/test_metering.py \
  docs/operations/metering.md
git commit -m "feat: meter hosted usage with quota safety"
```

### Task 9: Complete production readiness and incident response

**Files:**
- Create: `docs/operations/production-readiness.md`
- Create: `docs/operations/incident-response.md`
- Create: `docs/operations/runbooks/relay-outage.md`
- Create: `docs/operations/runbooks/action-security.md`
- Create: `docs/operations/runbooks/repair-disable.md`

- [ ] **Step 1: Populate the readiness checklist with evidence links**

Every item names owner, evidence, test/runbook link, last exercise, and expiry. Empty evidence is a
failure, not an implicit pass.

- [ ] **Step 2: Run tabletop scenarios**

Exercise stolen device, compromised host token, cross-tenant authorization attempt, relay outage,
database restore, bad migration, malicious repair candidate, GitHub App compromise, and leaked
artifact URL.

- [ ] **Step 3: Fix every critical/high gap**

Update controls, tests, alerts, or runbooks. Record any accepted medium/low risk with owner and
review date.

- [ ] **Step 4: Obtain production sign-off**

Required sign-offs: engineering, security, operations, privacy, product, and support. General
availability remains blocked until all critical/high readiness checks pass.

- [ ] **Step 5: Commit production readiness evidence**

```bash
git add docs/operations docs/security docs/privacy
git commit -m "docs: complete production readiness review"
```

## Completion gate

Run all local, cloud, security, load, chaos, restore, migration, and release-verification suites.
Expected: published SLOs have alerts and owners; tenant isolation fails closed; backup/restore meets
RPO/RTO; releases are signed and attested; and all critical/high readiness items have evidence.
