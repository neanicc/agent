# Production readiness

Decision: **NO-GO for public beta or general availability**

The implementation is production-oriented and locally testable, but code completion is not
production approval. Missing live-environment exercises, durable hosted adapters, provider
activation, legal terms, and human sign-offs remain blocking. Empty evidence is a failure.

## Evidence ledger

“Automated per build” means the linked test is repeatable; it does not stand in for the named
staging or human exercise.

| Gate | Owner | Evidence | Last exercise | Evidence expiry |
| --- | --- | --- | --- | --- |
| Local durability, redaction, replay, permissions | Local Runtime | `loopguard/tests/control`, `loopguard/tests/security`, `docs/security/threat-model.md` | Automated per build; human stolen-device exercise pending | Every release; tabletop before GA |
| Managed agent hooks and repository trust | Integrations | `loopguard/tests/adapters`, `docs/security/trust-boundaries.md` | Automated per build; compromised-token tabletop pending | Every adapter or agent-version change |
| Tenant isolation and RLS | Control Plane / Security | `services/control-api/tests/security`, `services/control-api/tests/test_tenant_isolation.py`, `services/control-api/tests/test_postgres_rls.py` | Automated per build; live PostgreSQL policy review pending | Every schema/auth change |
| Signed expiring idempotent actions | Actions / Security | `services/control-api/tests/test_actions.py`, `docs/operations/runbooks/action-security.md` | Automated per build; stolen-device tabletop pending | Every action-contract or key change |
| Stream ordering and reconnect | Streaming | `services/control-api/tests/test_session_stream.py`, checked-in k6 stream scenario | Automated unit gate; staging k6 pending | Every release and quarterly load exercise |
| Auto-Heal isolation and draft-only publication | Auto-Heal / Security | `services/control-api/tests/test_repair_workflow.py`, `loopguard/tests/heal`, `docs/operations/runbooks/repair-disable.md` | Automated per build; malicious-candidate/GitHub compromise tablettops pending | Every worker/sandbox/provider change |
| Retention, export, deletion | Privacy | `services/control-api/tests/test_privacy_workflows.py`, `docs/privacy/data-inventory.md` | Automated per build; production processor inventory review pending | Quarterly and every datastore change |
| SLOs, bounded telemetry, alerts | Operations | `loopguard/tests/test_telemetry.py`, `services/control-api/tests/test_telemetry.py`, `docs/operations/slos.md` | Automated schema tests; production alarms unverified | Every release; alert exercise quarterly |
| Capacity and overload | Operations | `services/control-api/tests/test_capacity.py`, `services/control-api/tests/chaos`, checked-in k6 scenarios | Deterministic local gates; production-equivalent staging load pending | Every release/capacity change |
| Backup, restore, and migration | Database / Operations | `services/control-api/tests/operations/test_restore.py`, `loopguard/tests/security/test_migrate.py`, `docs/operations/disaster-recovery.md` | Automated integrity tests; encrypted production-like restore pending | Monthly restore; every schema release |
| Signed releases and SBOM provenance | Release / Security | `services/control-api/tests/operations/test_release.py`, pinned release/security workflows | Static/local verification; protected signed release pending | Every release |
| Deployable AWS/Kubernetes topology | Platform / Security | Terraform, Helm, rendered-manifest policy tests, deployment guide | Static policy tests; Terraform/Helm validation and staging apply pending | Every infrastructure change |
| Regional failover and failback | Operations / Database | two-region Terraform, operator rehearsal script, deployment guide | Not exercised | Quarterly; mandatory before GA |
| Metering, quotas, billing reconciliation | Product / Finance | `services/control-api/tests/test_metering.py`, `services/control-api/tests/test_billing.py`, `docs/operations/billing.md` | Automated per build; real provider outage/reconciled staging invoice pending | Every catalog/provider change |
| Enterprise OIDC/SAML and SCIM | Identity / Security | `services/control-api/tests/test_enterprise_identity.py`, `docs/operations/support.md` | Automated domain/token/provisioning tests; real IdP/deprovision outage exercise pending | Every identity-provider change |
| Consent-bound support and break glass | Support / Security | `services/control-api/tests/test_support_access.py`, `docs/operations/support.md` | Automated scope/preview/dual-approval tests; abuse tabletop pending | Quarterly and every support-policy change |
| Public docs, accessibility, and generated references | Developer Experience | docs unit/e2e tests, generated OpenAPI/CLI/docs indexes | Automated per build and local external-link crawl; deployed public-URL crawl pending | Every docs or contract change |
| First-party dependency and source scanning | Security | `security.yml`, npm audits, pip-audit, Bandit | Production web/bridges report zero npm vulnerabilities; Python dependencies report none; Bandit reports no high findings. Eight reviewed medium heuristics remain (bounded/internal XML parsing, allowlisted SQL identifiers, and intentional container tmpfs) | Every dependency/source change; medium review before GA |
| Deprecated Expo compatibility fallback | Client / Security | `cloud-app/README.md`, compatibility test and bundle build | Build/test pass, but the frozen Expo 51 dependency graph reports 28 advisories (1 critical, 14 high, 12 moderate, 1 low). It is excluded from production artifacts and must not process untrusted/public traffic | Remove or complete a separately tested framework upgrade before distributing this fallback |
| License, terms, privacy notice, trademark | Owner / Legal | No license exists | `awaiting_owner_legal_choice` | Blocking before distribution/billing |

## Tabletop status

The required scenarios have runbooks, automated controls, or exercise prompts, but no human exercise
is fabricated:

| Scenario | Prepared evidence | Status |
| --- | --- | --- |
| Stolen device | action-security runbook, action/device tests | `awaiting_human_tabletop` |
| Compromised host token | threat model, hook/auth abuse tests | `awaiting_human_tabletop` |
| Cross-tenant authorization attempt | tenant-isolation and abuse tests | `awaiting_human_tabletop` |
| Relay outage | relay runbook and chaos test | `awaiting_staging_exercise` |
| Database restore | DR guide and restore integrity suite | `awaiting_isolated_restore` |
| Bad migration | expand/read-switch/contract rehearsal scripts | `awaiting_production_scale_rehearsal` |
| Malicious repair candidate | repair-disable runbook and sandbox tests | `awaiting_human_tabletop` |
| GitHub App compromise | repair-disable runbook and draft-only policy tests | `awaiting_human_tabletop` |
| Leaked artifact URL | threat model, artifact authorization/expiry tests | `awaiting_human_tabletop` |
| SCIM deprovision | enterprise identity tests | `awaiting_real_idp_exercise` |
| Enterprise IdP outage | incident-response procedure | `awaiting_real_idp_exercise` |
| Billing-provider outage | billing fail-closed/reconciliation tests | `awaiting_provider_sandbox_exercise` |
| Support-access abuse | support tests and incident procedure | `awaiting_human_tabletop` |
| Primary-region failover/failback | two-region definitions and operator script | `awaiting_staging_dr_exercise` |

Use [the exercise record](tabletop-exercises.md) for each run. A failed target creates a blocking
defect; it is not waived in the meeting.

## Sign-offs

All sign-offs are `awaiting_human_signoff`:

| Role | Must review |
| --- | --- |
| Engineering | cumulative tests, architecture, durable adapters, migrations, rollback |
| Security | threat model, isolation, signing, support/identity, release and infrastructure evidence |
| Operations | SLOs, alerts, capacity, deploy/rollback, restore, regional exercise |
| Privacy | data inventory, processors, retention, export/deletion, incident handling |
| Product | measured cohort gates, plan/catalog, limits, naming, roadmap boundaries |
| Support | channels, coverage, response expectations, consent and break-glass procedure |
| Legal/owner | license, terms, privacy notice, trademarks, provider/customer agreements |

## Path to GO

1. Implement durable transactional enterprise identity, support-access, billing, quota, and workflow
   adapters and prove hosted startup with them.
2. Remove the deprecated Expo fallback or upgrade it onto an advisory-clean supported framework;
   never include its current dependency graph in a public artifact.
3. Run all release gates with PostgreSQL, Docker, k6, Terraform, Helm, browser, and iOS tooling.
4. Deploy signed digests to an authorized production-equivalent staging account.
5. Complete load, restore, migration, provider outage, support abuse, and regional failover/failback
   exercises with dated non-sensitive evidence.
6. Resolve every critical/high finding and repeat failed exercises.
7. Obtain legal terms and all named sign-offs.
8. Activate a bounded cohort, collect the product thresholds in the master plan, and promote only if
   the observed evidence passes.

No implementation agent may self-sign, apply production infrastructure, promote a database, switch
production DNS, activate billing, publish an app/package, or notify customers.
