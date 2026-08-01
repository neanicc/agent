# Incident response

This playbook covers confidentiality, integrity, availability, billing, release, and safety events
across local clients, the hosted control plane, Auto-Heal, and infrastructure.

## Declare and classify

Anyone may declare an incident. Start a restricted incident record with UTC timestamps, reporter,
affected build/digest, first known bad event, tenant/region scope, current impact, and incident
commander. Never paste prompts, source, secrets, raw artifacts, payment payloads, or customer
identifiers into a general channel.

| Severity | Definition | Initial response target |
| --- | --- | --- |
| SEV-0 | Cross-tenant access, signing/approval bypass, malicious release, or unsafe remote execution | Immediate page and containment |
| SEV-1 | Durable data loss/corruption, broad outage, leaked privileged credential, or confirmed privacy breach | Page on-call immediately |
| SEV-2 | Material degraded service, bounded provider outage, billing integrity failure, or missed SLO | Page or business-hours escalation by impact |
| SEV-3 | Minor defect with safe workaround and no security/data risk | Track through normal triage |

Targets become contractual only after owners approve support coverage. A cross-tenant result,
accepted-and-lost event/action, corrupted restore, or signature bypass is always an incident even
when aggregate SLOs look healthy.

## Roles

- Incident commander owns severity, priorities, authority requests, and handoffs.
- Operations lead contains traffic and coordinates recovery.
- Security lead preserves evidence, revokes credentials, and assesses disclosure.
- Product/support lead communicates impact without speculation.
- Scribe records decisions, timestamps, commands, evidence hashes, and owners.
- Privacy/legal owner decides regulatory and customer notification; an implementation agent cannot.

No person both requests and approves break glass. Production database promotion, DNS failover,
credential rotation affecting users, destructive cleanup, or customer notification requires the
named human authority.

## Response loop

1. Stabilize people and access: establish the record, restrict communications, preserve immutable
   logs, and prevent evidence expiration.
2. Contain: disable the narrowest affected capability, revoke exact credentials, fence writers, or
   stop repair publication. Keep local guarding and export/deletion available.
3. Investigate: build a UTC timeline from bounded IDs and signed audit records. Separate fact,
   hypothesis, and unknown.
4. Eradicate: fix the root cause, rotate compromised material, and prove the fix in isolation.
5. Recover: use canaries, explicit build/digest checks, SLO alarms, replay/integrity probes, and a
   documented rollback point. Do not recover by bypassing signature or tenant controls.
6. Communicate: state impact, affected interval, mitigation, user action, and next update. Do not
   attribute cause before evidence supports it.
7. Review: within five business days, record contributing conditions, detection gaps, corrective
   actions, owners, deadlines, and which test/runbook now prevents recurrence.

## Evidence handling

Use correlation IDs, immutable audit records, redacted structured logs, deployment digests,
database/object integrity manifests, and signed release attestations. Hash exported evidence and
record custody. Diagnostic bundles require tenant consent unless dual-approved break glass grants
metadata-only access. Apply retention and legal-hold policy; never keep copied customer content as
an informal incident archive.

## Capability-specific first actions

- Relay outage: follow [relay outage](runbooks/relay-outage.md).
- Suspicious action or signing event: follow [action security](runbooks/action-security.md).
- Unsafe repair candidate or compromised GitHub App: follow
  [repair disable](runbooks/repair-disable.md).
- Database corruption/restore: follow [disaster recovery](disaster-recovery.md).
- Regional loss: follow the regional recovery and operator rehearsal in
  [deployment](deployment.md).
- Billing-provider outage: preserve immutable usage, block invoice finalization, keep local/read/
  export/delete/security paths available, and reconcile before resuming.
- Enterprise IdP outage: do not weaken authentication; use already-valid sessions only within
  policy, preserve SCIM deprovision queue, and reconcile before reopening provisioning.
- Support-access abuse: revoke consent/sessions, preserve audit, alert the tenant, and investigate
  approver separation and preview hashes.

## Communication templates

Initial: “We are investigating [observable impact] beginning [UTC]. [Scope] is affected. We have
contained [verified fact]. Next update by [UTC].”

Resolution: “Service recovered at [UTC] after [verified mitigation]. The affected interval and user
action are [facts]. We are completing integrity and security review; a follow-up will describe
corrective actions.”

Privacy, legal, regulator, law-enforcement, and public statements require their human owners.
