# LoopGuard data inventory

Owner: Privacy Engineering
Default policy source: `loopguard_api.retention.DEFAULT_RETENTION_DAYS`
Deletion workflow owner: Control Plane

| Data/object | Purpose | Classification | Source/processor | Default retention | Export | Tenant deletion |
|---|---|---|---|---:|---|---|
| Tenant/user/membership IDs and roles | Authentication and authorization | Confidential | OIDC + PostgreSQL | Account life | Yes | Delete; keep deidentified tombstone |
| Host/repository/device public identity | Trust and capability binding | Confidential | Local daemon/control API | Account life | Yes | Delete and revoke |
| Device/host private keys | Signing/authentication | Secret | OS Keychain/DPAPI/libsecret; never cloud-exported | Until revoke | No | Delete from device/host |
| Events and sequence metadata | Guard evidence and replay | Restricted content + integrity metadata | Local daemon, relay, PostgreSQL | 30 days hosted; local user-controlled | Yes, redacted | Delete |
| Prompts/source/tool input/output | Optional evidence | Restricted tenant content | Local hooks/artifacts | 30 days when retained | Yes, redacted | Delete |
| Changes and verification proof | Explain deterministic outcomes | Confidential | Verification workers | 90 days | Yes | Delete |
| Repair intake, fixture, candidate, checks | Reproduce/rank a failure | Restricted tenant content | Temporal/isolated worker | 90 days | Yes, sanitized | Delete rows, search attributes, objects |
| Artifacts and hashes | Evidence delivery/integrity | Restricted object; hash is confidential metadata | S3-compatible storage + KMS | 30 days unless proof policy extends | Manifest + content | Delete object versions/replicas |
| Signed action/challenge/receipt | Explicit remote-control audit | Integrity-critical confidential metadata | User device, API, host | 365 days with audit | Yes | Delete content; tombstone legal minimum |
| Audit entries | Accountability/security investigation | Confidential | Control API | 365 days | Yes | Deidentify or delete per legal policy |
| Push token and opaque route ID | Notification delivery | Confidential credential-like identifier | APNs/device | Until revoke/logout/bounce | No raw token | Delete immediately |
| Usage/provider cost | Product cost visibility | Confidential financial metadata | Provider/worker ledger | 365 days | Yes | Delete or legally deidentify |
| Billing customer/subscription/invoice IDs | Hosted billing/reconciliation | Confidential financial metadata | Billing provider; no card data | Legal/accounting policy | Yes | Delete provider link; retain legal records |
| Derived metrics | Capacity/SLO without content | Internal; tenant-linked until aggregation | Telemetry pipeline | 30 days raw, aggregate policy | Tenant-visible usage only | Delete tenant dimensions |
| Support diagnostic bundle | Consent-bound troubleshooting | Restricted, redacted | Tenant-generated | Scope expiry, at most 7 days | Yes | Delete at expiry/request |
| Deletion tombstone | Prove workflow completion | Deidentified compliance metadata | Privacy workflow | 2,555 days | Status only | Retain HMAC tenant hash, time, workflow ID; no payload |

## Workflow guarantees

Export is tenant-scoped, deterministic-manifested, and excludes access/refresh tokens, private
keys, credentials, authorization headers, and presigned URLs. The caller encrypts the resulting
bundle for delivery; an export URL is short-lived and audience-bound.

Deletion checkpoints PostgreSQL, object storage/replicas, Temporal search attributes, notification
tokens, and tenant-derived metrics separately. It is idempotent and resumes after interruption
without repeating completed destructive stages. Completion writes only an HMAC tenant reference,
workflow ID, completion time, and stage count.

Legal hold is explicit per record and prevents retention expiry; it does not silently grant
support access. Changing retention policy is versioned, audited, and never shortens a legal
minimum without privacy/legal approval.
