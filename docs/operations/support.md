# Support access and customer help

LoopGuard support is designed around evidence the tenant deliberately shares. There is no silent
impersonation and no support role that can approve or execute a remote action.

## Before hosted support is offered

The product owner must publish an actual support channel, coverage window, severity definitions,
and response commitments. Those values are not approved in this repository, so public hosted
support remains **NO-GO**. Security reports use the private process in
[SECURITY.md](../../SECURITY.md), not a support ticket.

Community bug reports may use the repository issue form. Users must review `loopguard doctor
--json` output and remove prompts, source, secrets, personal data, tenant IDs, and production URLs
before attaching it.

## Normal support access

Tenant administrators can grant time-bounded access for:

- `metadata_health`: allowlisted service, build, capability, count, region, and health metadata;
- `diagnostic_bundle`: only the exact redacted preview whose hash the tenant approved.

Consent requires a reason, ticket ID, scope, and expiry between five minutes and 24 hours.
Diagnostic access recomputes the redacted bundle and compares it with the approved preview hash.
Any difference fails closed. Revocation or expiry immediately invalidates the consent and session.

Prompts, source, patches, artifacts, secrets, credentials, raw environments, action approvals, and
action execution are excluded. Access and denial events are immutable audit records.

## Break glass

Break-glass access is limited to metadata health, expires within one hour, and requires two
different support approvers. The tenant is alerted when the second approval grants access. The
request, both approvals, session opening, reads, expiry, and revocation are audited. Break glass
does not unlock diagnostics or remote actions.

Use break glass only for a declared incident where waiting for ordinary tenant consent would
materially increase harm. Link the incident and ticket, state the exact tenant and reason, and
revoke immediately after the bounded investigation.

## Enterprise identity

SCIM bearer secrets are displayed once, stored as salted scrypt hashes with a deployment pepper,
scoped to one tenant, rotatable, revocable, and expiring. SCIM create/update operations require an
idempotency key; reusing a key with different input fails. Deprovisioning is soft and removes
effective access without deleting audit evidence.

OIDC or SAML configuration requires a verified DNS TXT challenge and an exact HTTPS provider
identifier. Group-to-role mapping is explicit policy. An email domain alone never authorizes a
role.

The checked-in identity and support stores are deterministic test adapters. Hosted startup rejects
them; production must inject durable transactional adapters and exercise provider outage,
deprovision, rotation, consent, abuse, and expiry paths before GA.
