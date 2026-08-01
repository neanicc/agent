# Runbook: suspicious remote action

Owner: Security and Actions on-call
Trigger: signature failures, replay bursts, cross-tenant result, unexplained approval, stolen
device, compromised host token, or action audit mismatch

## Contain

1. Declare SEV-0 for cross-tenant access, signature/approval bypass, or unsafe execution.
2. Preserve the signed envelope, action ID, target version, challenge ID, actor/device IDs,
   correlation IDs, audit sequence, build digest, and UTC timestamps. Do not copy action payload
   content into a general incident channel.
3. Revoke the exact device/host/token. If scope is unknown, disable remote action admission while
   preserving local guarding and read/export/delete/security access.
4. Do not “fix” an ambiguous result by creating another action. Reconcile the existing ID with the
   authoritative host outcome and audit chain.
5. Rotate signing material only under the security-owner procedure; account for offline clients and
   reject the old key after the documented overlap.

## Investigate

Verify tenant and repository binding, principal role, policy, target kind/ID, expected state
version, expiry, challenge single use, canonical bytes, signature key, idempotency input, dispatch
receipt, host resolution, and immutable audit ordering. Compare the request against security abuse
tests; add a synthetic regression before restoring admission.

## Recover

Deploy only a signed release, canary with fake actions, verify audit continuity and replay defense,
then enable the narrowest tenant/action category first. Alert affected tenants through the approved
human communication process. Privacy/legal owners decide disclosure.

Exit only when compromised credentials are revoked, unauthorized work is bounded, every action is
reconciled, regression tests pass, and monitoring covers the root cause.
