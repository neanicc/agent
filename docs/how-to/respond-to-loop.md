# How to respond when LoopGuard stops an agent

A stop is a safety decision, not proof that the agent is wrong. Read the evidence before choosing
an action.

## Check the pattern

Open the session in the web or iOS Runs view. Confirm whether LoopGuard detected:

- an exact repeated tool call;
- semantically equivalent work;
- a ping-pong between two states;
- repeated failure without new evidence; or
- a configured time, turn, or cost budget.

Secret-like values are redacted. If the timeline is incomplete or replay is reconciling, wait for
an explicit current state instead of guessing.

## Choose the narrowest safe response

- Interrupt when repeated work has no useful next step.
- Redirect with a concrete new constraint or missing fact.
- Request evidence when the agent’s claim is unproven.
- Approve only the exact bounded action shown in the preview.
- Start repair only for an eligible pipeline failure in a trusted repository.

Remote actions are signed, expire, bind to an expected state version, and are idempotent. If a
client loses the response, reconcile the existing action ID. Do not create a second action merely
because the network timed out.

## If the stop looks wrong

Preserve the detector category and redacted event IDs, then compare the policy and threshold active
for that repository. Do not paste source or prompts into a public issue. Run:

```bash
loopguard doctor --json
```

Review and redact the output before attaching it to a bug report. A false stop should be fixed with
a reproducible synthetic sequence and regression test, not by globally disabling protection.

## Emergency local behavior

The hosted service is not required for the local circuit breaker. During a relay outage, keep the
daemon running locally, do not approve ambiguous remote actions, and follow the
[relay outage runbook](../operations/runbooks/relay-outage.md).
