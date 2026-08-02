# Runbook: relay outage

Owner: Relay / Control Plane on-call
Trigger: relay acknowledgement, ingest availability, outbox age, or reconnect SLO alert

## Safety invariants

Local guarding continues offline. Never return success before durable cloud acceptance. Never
discard or reorder a local outbox record to reduce queue depth. Remote actions with ambiguous state
are reconciled by ID and are not blindly retried.

## Respond

1. Declare the incident and record build SHA, region, first alert, queue depth/age, ingest latency,
   database pool, Temporal backlog, and provider status.
2. Confirm whether failure is client connectivity, authentication/signature rejection, API
   overload, database/object storage, stream delivery, or regional infrastructure.
3. If acceptance is unsafe, keep explicit `429`/retry behavior or stop cloud admission. Do not
   disable local protection.
4. Bound a noisy tenant with existing admission controls. Do not raise global limits without a load
   result and rollback value.
5. Roll back the application only to a signed compatible digest. For regional loss, require writer
   fencing and the regional recovery evidence before DNS change.
6. On recovery, replay durable outboxes and verify monotonic cloud ingest, session replay cursor,
   zero duplicate events, action reconciliation, and queue drain slope.

## Exit criteria

The SLO is healthy through a full alert window; oldest durable outbox age is normal; synthetic
ingest and reconnect pass; no accepted event/action was lost; duplicate conflicts are zero or
explained; and the incident has an owner for every follow-up.

Exercise in staging with `tests/chaos/test_relay_recovery.py` plus production-equivalent k6 ingest
and stream scenarios. Record the build, dataset, result, and rollback. A local unit test is not a
regional exercise.
