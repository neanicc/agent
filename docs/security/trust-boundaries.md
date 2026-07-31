# LoopGuard trust boundaries

Status: enforced production baseline  
Owner: Security Engineering  
Review cadence: every behavior-changing release and at least quarterly

LoopGuard treats every transition below as an authenticated, validated boundary. A deployment
control outside this repository is described as an operational dependency, never assumed.

| Boundary | Untrusted side | Trusted side | Required control | Automated evidence |
|---|---|---|---|---|
| Agent hook → local daemon | Repository content, hook JSON, paths, tool output | Owner-only local state and event log | Bounded schemas, path containment without symlinks, redaction, peer identity, size limits | `loopguard/tests/security/test_local_abuse_paths.py`, `loopguard/tests/test_control_transport.py`, `loopguard/tests/test_redaction.py` |
| Local daemon → cloud ingest | Host process and network | Tenant-scoped relay/outbox | Short-lived host credentials, repository binding, ordered idempotent events, request limits | `services/control-api/tests/test_hook_ingest.py`, `test_pairing_relay.py`, `test_tenant_isolation.py` |
| Browser/iOS → control API | User-controlled client and identifiers | Tenant data and action challenge service | OIDC validation, role permission, per-object tenant lookup, explicit response models | `services/control-api/tests/test_auth.py`, `tests/security/test_abuse_paths.py` |
| User review → host execution | Browser/iOS tap, replayed request, stolen bearer token | Exact host-side effect | Expiring canonical challenge, paired-device signature, tenant/user/device binding, expected state version/hash, idempotent receipt | `services/control-api/tests/test_actions.py`, `test_repairs_api.py`, `apps/ios/LoopGuardTests/ActionSigningTests.swift` |
| Repair input/model → sandbox | Malicious failure payload, source, dependency, or model output | Isolated reproduction and candidate evidence | Sanitized fixtures, allowlisted commands, bounded patches, no credentials/network/cloud metadata, deterministic checks | `loopguard/tests/heal/test_fixtures.py`, `test_reproduce.py`, `test_candidates.py`, `test_evaluate.py` |
| Sandbox → GitHub | Candidate content and compromised dependency | Draft pull request only | Installation/repository allowlist, base SHA binding, signed request, draft-only publication, no merge/deploy token | `loopguard/tests/heal/test_github.py` |
| Application → PostgreSQL/object store | User IDs, cursors, artifact IDs | Tenant-scoped durable data | Tenant transaction context/RLS, parameterized access, tenant-prefixed object keys, envelope encryption, checksums | `services/control-api/tests/test_tenant_isolation.py`, `test_artifacts.py` |
| CI source → release | Pull request code and dependencies | Signed distributable | No publishing credentials in untrusted jobs, pinned dependencies/actions, SBOM, checksum, provenance and signature verification | `scripts/verify_release.sh` and release workflow (Production Hardening Task 7) |
| Operator/support → tenant | Cloud operator or support user | Tenant metadata/content | Least privilege, explicit consent for diagnostics, bounded access, immutable audit; no silent impersonation or action approval | Production Hardening Task 10 tests and runbook |

## Identity domains

- Tenant, user, device, host, repository, session, event, action, repair, and artifact identifiers
  are independent domains. A valid identifier in one domain never authorizes another.
- Public resources use unpredictable UUIDs or opaque random IDs. Unpredictability is not
  authorization; each lookup includes tenant context.
- Bearer identity proves an authenticated principal. Device signatures separately prove that the
  exact reviewed action came from the paired user's key. Both are required for execution.
- Forwarded headers are trusted only from configured proxy networks. Host names are allowlisted.
  Production TLS termination and request-size enforcement are deployment controls verified by the
  infrastructure policy tests.

## Data handling at boundaries

Prompts, source, raw tool arguments/output, credentials, private keys, full artifact bodies, and
payment details are prohibited from logs, metrics, traces, push messages, URLs, and action labels.
They may enter encrypted, tenant-scoped evidence storage only when required by the product and
retention policy. Cross-boundary errors use stable codes and request IDs, not internal exception
text.

## Failure rule

Unknown tenant ownership, stale capability, invalid signature, missing state proof, ambiguous
delivery, unavailable durable adapter, unsafe local permissions, and sandbox uncertainty all fail
closed. Availability degradation may delay hosted features; it must never disable local guarding,
silently drop accepted data, rerun a side effect, or reinterpret existing state.
