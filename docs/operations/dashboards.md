# Observability dashboards and alerts

Every panel links to its SLO and runbook. Dashboard variables use environment, region, service,
release digest, and bounded workflow/category values—never tenant/user/resource IDs as metric
dimensions.

## Control-plane overview

- request rate, 2xx/4xx/5xx and explicit overload by route template;
- p50/p95/p99 duration, pool saturation, statement timeout, queue depth;
- ingest batch bytes/events, idempotent duplicates, conflict rejects, relay lag;
- stream connections, queued messages, replay duration, cursor gaps;
- current/previous signed image digest and canary cohort.

Alerts: API availability/latency burn, database pool >80% for 10 minutes, sustained overload below
tested capacity, outbox age >30 seconds, stream queue at 80%, and unexpected cardinality growth.

## Actions and device trust

- challenges created/expired/stale/revoked by bounded action kind;
- signature and expected-state rejection rate;
- queued/delivered/executing/reconciling/terminal age;
- host offline and acknowledgement-loss reconciliation;
- pairing, rotation and revocation outcomes.

Alerts: action-resolution burn, any signature-validation regression after deploy, reconciling over
five minutes, or execution receipt without a matching signed action.

## Verification and repair

- eligible checks, duration, deterministic verdict and artifact failures;
- repair stage age, reproduction/candidate/check/ranking result;
- isolated worker queue/capacity, timeout, egress denial and resource-limit termination;
- awaiting-publication age, draft publication/reversion and rollout gates.

Alerts: workflow SLO burn, worker saturation, isolation-policy violation (security page), action
wait beyond configured deadline, or publish mode active while a safety gate is false.

## Data, privacy and billing

- object/row growth by category, retention deletion age and failures;
- export/deletion checkpoint age, tombstone completion and resume count;
- backup age/checksum/restore rehearsal RPO/RTO;
- immutable usage ingestion, reservation utilization, invoice reconciliation difference.

Alerts: retention or deletion interruption over 24 hours, backup age beyond RPO, restore integrity
failure, non-zero invoice reconciliation difference, or provider-webhook signature failure spike.

## Trace/log handling

Search uses request, event, session, action, or workflow IDs from an authenticated support case.
Structured logs carry stable code, bounded route, status, duration, release and region. The
telemetry library rejects forbidden attribute names and unbounded metric labels in tests.
Collector processors additionally drop keys matching payload, prompt, source code, tool args,
tool output, token, secret, credential, authorization, and private key before export.

Dashboard access uses SSO, least-privilege roles, audit logs and short sessions. Tenant users never
receive the operator observability backend; customer-visible activity comes from the tenant-scoped
control API.
