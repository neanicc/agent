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
        json={
            "target": {"kind": "session", "target_id": str(tenant_a_session.id)},
            "kind": "interrupt",
        },
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

This task validates and extends controls already required in the foundation/owning subsystem
plans; it is not the first time local permissions, transport identity, repository trust, action
signatures, sandbox isolation, or tenant RLS are implemented. Any critical threat-model control
missing from its earlier owner is moved into that earlier plan before implementation proceeds.

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
- Create: `loopguard/src/loopguard/security/migrate.py`
- Test: `loopguard/tests/security/test_permissions.py`
- Test: `loopguard/tests/security/test_update.py`
- Test: `loopguard/tests/security/test_migrate.py`
- Create: `CHANGELOG.md`
- Create: `docs/reference/versioning.md`
- Create: `docs/migrations/demo-v1-to-control-v1.md`

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

Adopt semantic versioning for packages and explicit versioning for event/protocol/API/database
contracts. Every breaking or behavior-changing release updates `CHANGELOG.md`, compatibility
matrix, deprecation schedule, and a tested migration guide. Deprecation warnings include the old
surface, replacement command/API, removal version/date, and docs URL.

Expose `loopguard update check`, `loopguard migrate check`, and `loopguard migrate apply`.
`check` is read-only and machine-readable. `apply` creates/verifies an encrypted backup, previews
steps, validates free space/version compatibility, uses resumable forward migrations, and refuses
downgrade/data reinterpretation without an explicit documented path. Failed migrations leave the
old version usable or provide an exact restore command. Add fixture migrations from the existing
demo/prototype state and each supported schema version.

- [ ] **Step 4: Run local security tests**

Run: `cd loopguard && python -m pytest -q tests/security`
Expected: PASS.

- [ ] **Step 5: Commit local hardening**

```bash
git add loopguard/src/loopguard/security loopguard/tests/security CHANGELOG.md \
  docs/reference/versioning.md docs/migrations/demo-v1-to-control-v1.md
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
    assert count_active_tenant_rows(tenant.id) == 0
    assert list_tenant_objects(tenant.id) == []
    assert only_deidentified_legal_tombstones_remain(tenant.id)


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
def test_restore_preserves_stream_positions_and_artifact_hash(restored_environment, source_snapshot):
    restored = restored_environment.restore(source_snapshot)
    assert restored.max_cloud_ingest_seq == source_snapshot.max_cloud_ingest_seq
    assert restored.session_sequences == source_snapshot.session_sequences
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

Run: `scripts/verify_release.sh artifacts/release/manifest.json`
Expected: FAIL with a list of missing release evidence.

- [ ] **Step 3: Implement reproducible CI release**

CI uses pinned actions by commit, least-privilege permissions, isolated build jobs, dependency
review, secret scanning, SAST, container scanning, SBOM generation, Sigstore signing, provenance
attestation, and environment approval for promotion. Provider credentials are never available to
jobs that execute untrusted repository code.

All package-specific outputs (`loopguard/dist`, service/client images by digest, web provenance,
and iOS verification archive/checksum) are copied or referenced through
`artifacts/release/manifest.json`. The manifest names artifact type, canonical source path or
digest, SHA-256, SBOM, provenance, signature, build commit, and platform. The verifier resolves
paths relative to the manifest, rejects files outside the release root, and never assumes a
top-level `dist/`.

After license/ownership and environment approval, release jobs publish the Python package to its
configured registry, `@loopguard/playwright` to the configured npm registry, and signed service/web
images to ECR. Registry names/ownership are verified during setup and remain configurable until
the owner reserves them. Ordinary PRs and implementation agents build/verify only; they never
publish. Document `uvx loopguard quickstart`, `pipx install loopguard`, and pinned/versioned install
commands only after registry smoke tests prove those exact names.

- [ ] **Step 4: Build a release candidate and verify it**

Expected: `scripts/verify_release.sh artifacts/release/manifest.json` exits 0 and validates every
published artifact.

- [ ] **Step 5: Commit release security**

```bash
git add .github/workflows scripts/verify_release.sh docs/operations/release-process.md
git commit -m "ci: sign and attest loopguard releases"
```

### Task 8: Add metering, billing reconciliation, and quota safety

**Files:**
- Create: `services/control-api/src/loopguard_api/metering.py`
- Create: `services/control-api/src/loopguard_api/quotas.py`
- Create: `services/control-api/src/loopguard_api/billing.py`
- Create: `services/control-api/src/loopguard_api/routes/billing.py`
- Create: `services/control-api/alembic/versions/0003_billing.py`
- Create: `services/control-api/tests/test_metering.py`
- Create: `services/control-api/tests/test_billing.py`
- Create: `docs/operations/metering.md`
- Create: `docs/operations/billing.md`

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

Implement a versioned price catalog and tenant subscription/invoice state. The concrete hosted
reference uses Stripe Checkout/Customer Portal and signature-verified, replay-safe webhooks, while
the billing domain stays behind a provider interface. Store provider IDs, not payment details.
Webhook event IDs are idempotent; subscription/entitlement changes use provider event ordering and
reconciliation jobs. Customer-visible usage and invoice lines reconcile to the immutable usage
ledger and catalog version. Credits/refunds/adjustments are append-only audited records.

Billing failure never disables local guarding or deletes evidence. It can prevent new hosted
expensive work after a grace policy, while remote read/export/deletion and security actions remain
available. Test forged/replayed/out-of-order webhooks, duplicate usage, currency/rounding, price
change mid-period, quota race, provider outage, failed payment/grace, refund, tenant deletion, and
invoice reconciliation.

- [ ] **Step 4: Run metering tests and reconciliation fixture**

Run: `cd services/control-api && python -m pytest -q tests/test_metering.py tests/test_billing.py`
Expected: PASS.

- [ ] **Step 5: Commit metering controls**

```bash
git add services/control-api docs/operations/metering.md docs/operations/billing.md
git commit -m "feat: meter and bill hosted usage safely"
```

### Task 9: Define reproducible hosted infrastructure and deployment

**Files:**
- Create: `services/control-api/Dockerfile`
- Create: `infra/terraform/modules/control-plane/versions.tf`
- Create: `infra/terraform/modules/control-plane/main.tf`
- Create: `infra/terraform/modules/control-plane/variables.tf`
- Create: `infra/terraform/modules/control-plane/outputs.tf`
- Create: `infra/terraform/environments/staging/main.tf`
- Create: `infra/terraform/environments/staging/backend.hcl.example`
- Create: `infra/terraform/environments/production/main.tf`
- Create: `infra/terraform/environments/production/backend.hcl.example`
- Create: `infra/terraform/environments/production-dr/main.tf`
- Create: `infra/terraform/environments/production-dr/backend.hcl.example`
- Create: `infra/helm/loopguard/Chart.yaml`
- Create: `infra/helm/loopguard/values.yaml`
- Create: `infra/helm/loopguard/values-staging.yaml`
- Create: `infra/helm/loopguard/values-production.yaml`
- Create: `infra/helm/loopguard/templates/control-api.yaml`
- Create: `infra/helm/loopguard/templates/web.yaml`
- Create: `infra/helm/loopguard/templates/worker.yaml`
- Create: `infra/helm/loopguard/templates/migration-job.yaml`
- Create: `infra/helm/loopguard/templates/network-policy.yaml`
- Create: `infra/tests/test_rendered_manifests.py`
- Create: `services/control-api/scripts/rehearse_region_failover.sh`
- Create: `.github/workflows/deploy.yml`
- Create: `docs/operations/deployment.md`

- [ ] **Step 1: Write failing infrastructure-policy tests**

```python
def test_workloads_are_non_root_and_resource_bounded(rendered_manifests):
    for workload in rendered_manifests.workloads:
        pod = workload["spec"]["template"]["spec"]
        assert pod["securityContext"]["runAsNonRoot"] is True
        for container in pod["containers"]:
            assert container["securityContext"]["allowPrivilegeEscalation"] is False
            assert container["resources"]["requests"]["cpu"]
            assert container["resources"]["requests"]["memory"]
            assert container["resources"]["limits"]["cpu"]
            assert container["resources"]["limits"]["memory"]


def test_database_is_private_and_backups_enabled(terraform_plan):
    database = terraform_plan.resource("aws_db_instance", "primary")
    assert database.values["publicly_accessible"] is False
    assert database.values["storage_encrypted"] is True
    assert database.values["backup_retention_period"] >= 7


def test_production_uses_signed_digest_not_latest(rendered_production):
    for image in rendered_production.images:
        assert "@sha256:" in image
        assert ":latest" not in image
```

- [ ] **Step 2: Run policy tests against missing infrastructure**

Run:

```bash
python -m pytest -q infra/tests/test_rendered_manifests.py
terraform -chdir=infra/terraform/environments/staging validate
helm lint infra/helm/loopguard
```

Expected: FAIL because the Terraform module, Helm chart, and rendered manifests do not exist.

- [ ] **Step 3: Implement least-privilege infrastructure and deployment workflow**

Use AWS as the concrete reference deployment so this task does not stop on an unspecified cloud
provider. Keep region, account IDs, domains, capacity, and retention configurable. Pin Terraform
and AWS provider versions. The module provisions a multi-AZ VPC, private EKS nodes, RDS PostgreSQL
with point-in-time recovery, versioned S3 artifact storage, KMS keys, ECR repositories, AWS Load
Balancer Controller/ACM ingress prerequisites, Route 53 records, CloudWatch/audit sinks, and EKS
Pod Identity or IRSA. Production and staging use separate AWS accounts, Terraform state buckets,
lock tables, databases, buckets, keys, and identity boundaries. Backend example files contain no
real account names or credentials.

Temporal Cloud, the OIDC identity provider, APNs, and customer DNS remain explicitly configured
external dependencies. Terraform accepts secret ARNs and endpoints for them but never creates or
stores their credentials. Local and CI validation use fakes. Document the exact human-owned setup
needed before the first staging apply.

The Helm chart creates control API, web, and Temporal worker deployments, a pre-upgrade migration
job, service accounts, NetworkPolicies, PodDisruptionBudgets, autoscaling,
readiness/liveness probes, resource requests/limits, topology spread, and non-root read-only
containers. Repair workers use a dedicated node pool/service account/namespace, deny-all
NetworkPolicy, no cloud metadata/service-account token, no hostPath or container socket, and a
gVisor RuntimeClass or isolated microVM worker backend.

Provision a warm disaster-recovery region separately from the multi-AZ primary: cross-region RDS
replica or continuously tested encrypted restore path, versioned S3 replication with checksum
verification, ECR replication, regional KMS keys, independent ingress/capacity, and Route 53
health/failover records. Stateful promotion is an explicit human-authorized workflow that fences
the old writer, promotes/updates endpoints, validates migrations/Temporal/object consistency, and
supports failback. Do not call a single-region multi-AZ deployment “regional failover.”
`rehearse_region_failover.sh` runs against isolated staging/DR and records RPO/RTO, data loss,
action/event idempotency, stream reconnect, and rollback.

`deploy.yml` authenticates to AWS with GitHub OIDC and a narrowly scoped deploy role, accepts only a
previously signed ECR image digest, requires environment approval for production, runs migration
rehearsal, deploys a canary, checks health/SLOs, promotes on success, and rolls back to the prior
digest on failure. It never stores long-lived AWS credentials in GitHub secrets.

- [ ] **Step 4: Validate plans and rendered manifests without deploying**

Run:

```bash
terraform fmt -check -recursive infra/terraform
terraform -chdir=infra/terraform/environments/staging init -backend=false
terraform -chdir=infra/terraform/environments/staging validate
terraform -chdir=infra/terraform/environments/production init -backend=false
terraform -chdir=infra/terraform/environments/production validate
terraform -chdir=infra/terraform/environments/production-dr init -backend=false
terraform -chdir=infra/terraform/environments/production-dr validate
helm lint infra/helm/loopguard
helm template loopguard infra/helm/loopguard -f infra/helm/loopguard/values-staging.yaml
helm template loopguard infra/helm/loopguard -f infra/helm/loopguard/values-production.yaml
python -m pytest -q infra/tests/test_rendered_manifests.py
```

Expected: all commands exit 0 without creating or changing cloud resources.

- [ ] **Step 5: Commit deployable infrastructure**

```bash
git add services/control-api/Dockerfile infra .github/workflows/deploy.yml \
  docs/operations/deployment.md services/control-api/scripts/rehearse_region_failover.sh
git commit -m "ops: define production control plane deployment"
```

### Task 10: Complete production readiness and incident response

**Files:**
- Create: `services/control-api/src/loopguard_api/enterprise_identity.py`
- Create: `services/control-api/src/loopguard_api/routes/scim.py`
- Create: `services/control-api/src/loopguard_api/support.py`
- Create: `services/control-api/tests/test_enterprise_identity.py`
- Create: `services/control-api/tests/test_support_access.py`
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `.github/ISSUE_TEMPLATE/bug.yml`
- Create: `.github/ISSUE_TEMPLATE/feature.yml`
- Create: `.github/pull_request_template.md`
- Create: `apps/web/src/app/(docs)/docs/[[...slug]]/page.tsx`
- Create: `apps/web/src/app/(docs)/docs/search/route.ts`
- Create: `apps/web/e2e/docs.spec.ts`
- Create: `docs/product/pricing-and-limits.md`
- Create: `docs/operations/production-readiness.md`
- Create: `docs/operations/incident-response.md`
- Create: `docs/operations/support.md`
- Create: `docs/operations/runbooks/relay-outage.md`
- Create: `docs/operations/runbooks/action-security.md`
- Create: `docs/operations/runbooks/repair-disable.md`

- [ ] **Step 1: Populate the readiness checklist with evidence links**

Every item names owner, evidence, test/runbook link, last exercise, and expiry. Empty evidence is a
failure, not an implicit pass.

Before GA, implement tenant-configured enterprise OIDC/SAML federation through the identity
provider integration, domain verification, and SCIM 2.0 user/group provisioning with bearer-token
hashing, rotation, replay/idempotency, tenant scoping, soft deprovision, and audit. Map groups to
LoopGuard roles through explicit policy; no email-domain-only authorization.

Implement support access without silent impersonation. Support sees tenant metadata/health only
under least-privilege roles; any diagnostic bundle requires tenant consent, bounded time/scope,
redaction preview, reason/ticket ID, and immutable audit. Source, prompts, secrets, artifacts, and
action approval are excluded by default. Break-glass access requires dual authorization, alerts
the tenant, expires automatically, and cannot execute remote actions.

Document the contribution path, local `all-dev` setup, focused/cumulative test commands, security
reporting, release cadence, support channels/response expectations, public roadmap boundaries, and
transparent hosted pricing/quota dimensions. Issue forms collect version, platform, a redacted
`doctor --json` bundle, reproduction, expected/actual result, and consent before logs.

Publish the repository Markdown as a version-labelled public docs surface with Quickstart,
Tutorials, How-to, Reference, Concepts, Security/Privacy, Operations, Migration, and Error Code
navigation. Add full-text search, stable heading anchors, canonical/version links, copy buttons,
and source/edit links. CI validates internal/external links, code snippets, CLI/API generated
reference drift, and search indexing. The dashboard and structured errors deep-link to exact docs
without exposing an authenticated page.

The repository currently has no license file. Before public source/package distribution, the owner
must supply approved open-source or proprietary/source-available terms. The implementation agent
records this as `awaiting_owner_legal_choice` and must not invent a license or publish packages
under ambiguous rights.

- [ ] **Step 2: Run tabletop scenarios**

Exercise stolen device, compromised host token, cross-tenant authorization attempt, relay outage,
database restore, bad migration, malicious repair candidate, GitHub App compromise, and leaked
artifact URL. Also exercise SCIM deprovision, enterprise IdP outage, billing-provider outage,
support-access abuse, and full primary-region failover/failback.

- [ ] **Step 3: Fix every critical/high gap**

Update controls, tests, alerts, or runbooks. Record any accepted medium/low risk with owner and
review date.

- [ ] **Step 4: Obtain production sign-off**

Required sign-offs: engineering, security, operations, privacy, product, and support. General
availability remains blocked until all critical/high readiness checks pass.

These are external human authority gates. An implementation agent may prepare evidence and mark a
gate `awaiting_human_signoff`, but must stop and report the named approver/evidence needed. It must
never sign on behalf of a role, fabricate an exercise date, perform a production apply/failover, or
submit an App Store release.

- [ ] **Step 5: Commit production readiness evidence**

```bash
git add services/control-api docs/operations docs/security docs/privacy docs/product \
  apps/web/src/app/'(docs)' apps/web/e2e/docs.spec.ts \
  CONTRIBUTING.md SECURITY.md CODE_OF_CONDUCT.md .github/ISSUE_TEMPLATE \
  .github/pull_request_template.md
git commit -m "docs: complete production readiness review"
```

## Completion gate

Run all local, cloud, security, load, chaos, restore, migration, and release-verification suites.
Run Terraform formatting/validation, Helm lint/template, and rendered-manifest policy tests without
deploying.
Expected: published SLOs have alerts and owners; tenant isolation fails closed; backup/restore meets
RPO/RTO; regional failover is actually rehearsed; releases are signed and attested; billing
reconciles; enterprise identity/deprovision and consent-bound support access pass; deployable
infrastructure validates; and all critical/high readiness items have evidence plus real human
sign-off.
