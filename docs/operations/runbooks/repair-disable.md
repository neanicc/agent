# Runbook: disable Auto-Heal safely

Owner: Auto-Heal / Security on-call
Trigger: malicious candidate, sandbox escape signal, GitHub App compromise, verification bypass,
unexpected repository write, or repair error-budget breach

## Contain

1. Declare the incident. Stop new repair admission and draft-PR publication; do not stop local
   LoopGuard protection.
2. Revoke or suspend the exact GitHub installation/token if compromise is suspected. Preserve
   installation, repository, commit, workflow, candidate, verification, and PR identifiers.
3. Drain or terminate workers according to sandbox policy. Never attach a host filesystem,
   container socket, service-account token, or cloud role to inspect a candidate.
4. Quarantine evidence by immutable digest. Do not execute candidate code outside the isolated
   repair environment.
5. Mark ambiguous publication outcomes for reconciliation by idempotency key; do not open a second
   PR.

## Prove recovery

Reproduce the abuse with a synthetic repository, fix the owning policy/sandbox/signature boundary,
and run repair eligibility, isolation, malicious-candidate, verification, approval, publication,
and duplicate-side-effect suites. Rotate the GitHub App credential if affected and verify repository
allowlists and draft-only permissions.

Restore in stages: deterministic reproduction without publication, candidate verification, explicit
approval, then one canary draft PR in an authorized test repository. Auto-merge and deployment stay
impossible by design.

Exit only after security approves the evidence, every ambiguous PR is reconciled, credentials are
accounted for, and SLO/error-budget monitors remain healthy.
