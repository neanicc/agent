# LoopGuard production threat model

Status: enforced baseline for staged production work
Method: STRIDE-style abuse cases mapped to code owners and regression tests
Security owner: Security Engineering
Accepted critical/high risk: none

## Scope and security objectives

This model covers the local daemon and hooks, hosted control API, web and iOS clients, PostgreSQL,
object storage, Temporal repair workflows, repair sandboxes, GitHub publication, billing,
notifications, CI/release, and operator/support access.

LoopGuard must preserve:

- tenant confidentiality and isolation;
- exact, ordered, durable evidence without silent loss or replay mutation;
- explicit user intent for every remote side effect;
- private device/host signing material;
- local guarding when cloud, identity, or billing is unavailable;
- draft-only, evidence-backed repair publication;
- auditable export, retention, deletion, and operator access.

Availability never outranks those properties. Unknown ownership, stale state, or ambiguous action
delivery fails closed.

## Assets and classification

| Asset | Classification | Primary protection |
|---|---|---|
| Source, prompts, transcripts, tool input/output | Restricted tenant content | Local redaction, encryption, tenant scope, retention |
| Host/device private keys and OIDC/session credentials | Secret | Platform keystore, non-exportability where supported, rotation/revocation |
| Events, verification proof, repairs and artifacts | Confidential integrity-critical | Ordered IDs, hashes, tenant storage, encrypted objects |
| Signed actions and execution receipts | Integrity-critical audit | Canonical payload, expiry, expected state, dual identity, idempotency |
| GitHub installation and publication authority | Secret/high impact | Repository allowlist, minimal scope, draft-only token |
| Usage, subscription and invoice ledger | Confidential financial | Append-only records, provider signature, reconciliation |
| Audit and deletion tombstones | Confidential compliance | Minimal payload, immutable append, hashed tenant reference |
| Release artifacts and update manifests | Public integrity-critical | Digest, SBOM, provenance, trusted signature |

## Threat actors

- a malicious repository, hook payload, or checked-in configuration;
- a compromised package, model, browser extension, host, device, or CI dependency;
- a tenant user exceeding their role or attempting cross-tenant access;
- an attacker holding a stolen bearer, host credential, presigned URL, or paired device;
- malicious or mistaken cloud/support operators;
- compromised GitHub, identity, billing, notification, or Temporal provider credentials;
- network attackers and automated resource-exhaustion clients.

## Critical and high abuse cases

| ID | Abuse path and impact | Control/mitigation | Owner | Automated evidence |
|---|---|---|---|---|
| TM-01 | Cross-tenant identifier authorizes or reads another tenant's session/action/artifact | Tenant is included in every lookup and DB transaction; existence is concealed with 404 | Control API | `services/control-api/tests/security/test_abuse_paths.py`, `test_tenant_isolation.py`, `test_artifacts.py` |
| TM-02 | Stolen bearer executes an action reviewed by another user/device | Acceptance binds tenant, requesting user, device, key, nonce, expiry, canonical bytes and expected state | Actions | `services/control-api/tests/test_actions.py`, `tests/security/test_abuse_paths.py` |
| TM-03 | Hook path traversal or symlink reads SSH/cloud secrets | Hook paths are relative, bounded, existing regular files below an owner-controlled non-symlink state root | Local daemon | `loopguard/tests/security/test_local_abuse_paths.py` |
| TM-04 | Hook/tool content injects secrets into logs/cloud/push | Structured redaction before persistence; metadata-only telemetry/push; bounded error responses | Local + Observability | `loopguard/tests/test_redaction.py`, `apps/ios/LoopGuardTests/NotificationSecurityTests.swift`, telemetry tests |
| TM-05 | Malicious repair code escapes or reaches credentials/cloud metadata | Isolated worker, command/path allowlists, no host mounts/socket/token, denied network, resource/time budgets | Repair | `loopguard/tests/heal/test_reproduce.py`, `test_candidates.py`, infrastructure policy tests |
| TM-06 | Model output publishes arbitrary code, merges, deploys, or republishes after timeout | Deterministic verification/ranking, exact-state signed approval, draft-only repository API, action-ID reconciliation | Repair + Actions | `loopguard/tests/heal/test_evaluate.py`, `test_github.py`, `services/control-api/tests/test_repairs_api.py` |
| TM-07 | Forged host/repository event poisons evidence or crosses repository trust | Paired host credentials, repository binding, signature, event idempotency/order and conflict rejection | Relay | `services/control-api/tests/test_hook_ingest.py`, `test_pairing_relay.py` |
| TM-08 | Compromised object URL leaks another artifact or bypasses expiry | Tenant-scoped lookup/key, envelope encryption, digest, short-lived audience-bound download | Artifacts | `services/control-api/tests/test_artifacts.py` |
| TM-09 | SQL/RLS bypass exposes or mutates another tenant | Tenant transaction context, ORM parameters, query/update/relationship/bulk-write guards | Data | `services/control-api/tests/test_tenant_isolation.py` |
| TM-10 | Dependency/CI compromise publishes an untrusted update | Untrusted jobs lack registry/cloud credentials; release evidence verifier requires checksum, SBOM, provenance and signature | Release | Production Hardening Task 7 |
| TM-11 | Cloud/operator support silently impersonates or extracts content | Metadata-only default, tenant consent, bounded diagnostic scope, redaction preview, immutable audit, dual-authorized break glass | Support | Production Hardening Task 10 |
| TM-12 | Quota/billing failure disables local safety or deletes evidence | Local guard is independent; hosted expensive work uses reservation/grace while read/export/delete/security remain available | Billing | Production Hardening Task 8 |

## Denial of service and recovery

Attackers may send large bodies, many streams/actions/workflows, expensive repair inputs, cursor
gaps, or duplicate provider events. The API enforces body/batch/connection/workflow limits and
returns an explicit retryable overload response; it never accepts then drops. Database pools,
outboxes, stream queues, and workers are bounded. Persistence-before-response ambiguity is covered
by immutable IDs and reconciliation. Backups, restore integrity, region recovery, and
expand/contract migration are exercised by Production Hardening Tasks 5, 6, and 9.

## Supply chain and deployment dependencies

Production requires strict hosts/origins, trusted proxy CIDRs, TLS at the ingress, private
databases/nodes, encrypted versioned objects, least-privilege workload identity, signed image
digests, non-root read-only containers, NetworkPolicies, and isolated repair workers. Temporal
Cloud, OIDC, APNs, GitHub, Stripe, DNS, and cloud credentials are external human-configured
dependencies. No credential values belong in Terraform state, source, test fixtures, logs, or
support bundles.

## Residual-risk process

There is no accepted critical/high risk in this baseline. A new critical/high finding blocks
release until it has an automated control or a documented owner-approved exception with expiry,
compensating controls, and review date. Medium/low risks use the production-readiness register.
Implementation agents may record `awaiting_human_signoff`; they may not approve an exception,
exercise, legal choice, or GA gate on behalf of a human owner.
