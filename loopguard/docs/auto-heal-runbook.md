# Auto-heal rollout and incident runbook

LoopGuard auto-heal is intentionally staged per organization. A new organization starts in
`observe`, where intake is normalized and measured but no repository code is executed. Promotion
is sequential and requires an explicit policy administrator:

1. `observe` → `reproduce` after at least 100 accepted failures and no open safety incidents.
2. `reproduce` → `generate` after more than 85% of at least 20 failures reproduce, at least 95% of
   20 fixture attempts are safe, and no incident is open.
3. `generate` → `publish-draft` after at least 20 candidate attempts, an 80% pass rate or better, a
   no-winner rate of 20% or less, no reverted repair, and no incident.

Promotion is an expected-state policy update, not an automatic response to a metric. A mode can be
demoted immediately without waiting for a threshold. Publication always creates a draft pull
request and still requires a fresh, signed `publish_repair` action for the exact repair state.

## What to monitor

Alert on intake deduplication, reproduction rate, safe-fixture rate, candidate pass rate,
no-winner rate, workflow cost and duration, publication rate, PR acceptance, PR reversion, and
reported incidents. Page the on-call operator for any credential exposure, sandbox escape,
cross-tenant read, unexpected non-draft publication, destructive patch, or unbounded workflow.
One incident blocks promotion; a reverted repair blocks draft-publication promotion.

## Cancel a repair

Create a signed `cancel_repair` action using the repair's current state version and hash. Confirm
the Temporal workflow reaches `cancelled` and the `release_resources` activity completes. If the
worker is unavailable, terminate the workflow from the Temporal control plane, quarantine its
worktree/sandbox, and record an incident before manual cleanup.

## Revoke credentials

1. Suspend the organization at the policy layer and demote it to `observe`.
2. Revoke the affected GitHub App installation token or uninstall the app for the repository.
3. Revoke hook credentials and device keys through their authoritative stores.
4. Rotate KMS references; never copy key material into the database or an incident ticket.
5. Invalidate queued publication actions and verify audit entries by opaque ID.

## Delete repair artifacts

Cancel the workflow first. Delete encrypted artifact objects through the retention/deletion
workflow, then delete wrapped data keys and verify tombstones. Remove isolated worktrees and
sandboxes by recorded resource ID. Preserve only policy-required, redacted audit metadata. Never
download raw fixtures to an operator workstation to inspect them.

## Respond to a bad patch or reverted PR

Demote the organization to `generate` or `reproduce`, stop all queued publications, and mark an
incident. Close the draft PR; if it was merged, revert it using the repository's normal protected
branch process. Preserve the candidate, evaluation, contract-delta, and publication artifact IDs.
Add a deterministic regression check that reproduces the escape, then require a fresh metric
window and explicit promotion.

## Disable the GitHub App

Suspend the installation in LoopGuard, revoke its token, and uninstall or suspend it in GitHub.
Verify that the app no longer has `contents:write` or `pull_requests:write`, that no publication
workflow is still running, and that repository webhooks reject the retired credential. LoopGuard
does not merge or deploy repairs; review branch protection and deployment logs separately if a
draft was changed outside LoopGuard.

## Recovery

Resolve the root cause, rotate affected credentials, delete unsafe artifacts, and add regression
coverage. Close the incident only after tenant isolation, sandbox isolation, idempotent replay,
draft-only publication, and bounded cancellation all pass. Promotion then restarts sequentially
from the organization's current demoted mode.
