# LoopGuard security policy

LoopGuard handles source-adjacent evidence and remote-control requests. Please do not disclose a
suspected vulnerability in a public issue, discussion, pull request, screenshot, or shared log.

## Report privately

Use [GitHub private vulnerability reporting](https://github.com/neanicc/agent/security/advisories/new)
for this repository. Include the affected commit/version, impact, prerequisites, minimal
reproduction, and any proposed mitigation. Redact tokens, prompts, source, tenant identifiers, and
customer data. If private reporting is unavailable, contact the repository owner through their
GitHub profile without sending exploit details, then agree on a private channel.

The security owner should acknowledge receipt within two business days, establish severity and a
coordination channel, and provide an initial disposition within five business days. These are
response targets, not a promise of a fix date. Reporters acting in good faith should avoid
destructive testing, persistence, lateral movement, privacy invasion, denial of service, or access
beyond the minimum needed to demonstrate the issue.

## Supported versions

Until the first signed public release, only the current default-branch commit is evaluated for
security fixes. A release support matrix will be added before public distribution. Unsigned
artifacts, development deployments, the legacy Expo demo, and modified builds are not production
release channels.

## Security model

The authoritative boundaries and abuse cases are documented in
[the threat model](docs/security/threat-model.md) and
[trust boundaries](docs/security/trust-boundaries.md). Critical reports include cross-tenant data
access, signature or approval bypass, arbitrary repair execution, secret exposure, update or release
provenance bypass, deletion failure, and support-access escalation.

The repository has no license file. Do not infer redistribution or production-use rights; the
distribution gate is `awaiting_owner_legal_choice`.
