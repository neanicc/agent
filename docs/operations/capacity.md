# Control-plane capacity and backpressure

LoopGuard accepts work only when it can preserve it. It never returns success for work that was
silently dropped. Limits are tenant-scoped where a noisy tenant could affect another tenant, and
`429 LGAPI-OVERLOADED` responses include both `Retry-After` and bounded deterministic jitter
guidance.

## Default production envelope

| Boundary | Default | Enforcement |
| --- | ---: | --- |
| Request body | 1 MiB | API middleware, before body processing |
| Relay/hook batch | 100 events | Relay validation |
| Artifact declaration | 5 GiB | Before a presigned upload is created |
| Action challenges | 60 / tenant / minute | Sliding-window admission |
| Active repair workflows | 10 / tenant | Admission released on terminal state |
| Live session streams | 20 / tenant | Connection-lifetime admission |
| WebSocket delivery page/queue target | 1,000 events | Deployment adapter configuration |
| Outbox dispatch batch | 100 records | Deployment adapter configuration |
| PostgreSQL pool | 10 + 10 overflow / API process | SQLAlchemy engine |
| PostgreSQL statement timeout | 5 seconds | Connection session setting |
| Temporal activities | 20 / worker | Worker deployment configuration |
| Overload retry window | 1–30 seconds | Stable tenant/resource jitter |

All values are explicit `LOOPGUARD_API_*` settings. Production changes require a load result,
dashboard review, and rollback value in the deployment change.

## Load gates

Run against a staging environment with production-equivalent PostgreSQL, object storage, OIDC,
Temporal, and telemetry:

```bash
cd services/control-api

LOOPGUARD_BASE_URL=https://api.staging.example \
LOOPGUARD_HOOK_KEY_ID=... \
LOOPGUARD_HOOK_SECRET=... \
LOOPGUARD_REPOSITORY_HANDLE=... \
k6 run load/k6-ingest.js

LOOPGUARD_WS_BASE_URL=wss://api.staging.example \
LOOPGUARD_SESSION_ID=... \
LOOPGUARD_ACCESS_TOKEN=... \
k6 run load/k6-stream.js
```

The ingest gate requires fewer than 0.1% failed requests, p95 below 250 ms, and p99 below 750 ms.
The stream gate requires the same connection latency bounds, at least 99.9% successful checks, and
connections that remain open for the probe interval. Credentials are passed only through the
runner's secret environment and must be short-lived staging credentials.

## Recorded baseline

No synthetic laptop result is represented as production capacity. The checked-in k6 scenarios are
the release gate; the authoritative result must be attached to each staging release with:

- build SHA, timestamp, region, instance and database sizes;
- dataset size, tenant count, arrival rate, connections, and duration;
- throughput, p50/p95/p99 latency, errors, pool utilization, queue depth, and worker utilization;
- first saturated resource and the exact next scaling trigger.

The initial defaults are conservative bootstrap values, not a capacity claim. Scale API replicas
when sustained CPU exceeds 65% or p95 exceeds 200 ms for 10 minutes. Scale workers when the oldest
Temporal task or outbox record exceeds 30 seconds for 5 minutes. Increase database capacity before
pool wait p95 reaches 100 ms; do not increase application pools beyond the database connection
budget. Keep at least 30% measured headroom at the release target.

## Recovery and overload behavior

- A relay retry after losing the ACK returns the original durable sequence and creates no duplicate
  event or outbox row.
- A restarted repair worker resumes after its last persisted activity and never repeats a completed
  activity's side effect.
- Action, workflow, and stream overload is isolated by tenant.
- Retry guidance is stable for a tenant/resource pair, which spreads clients without making retries
  unpredictable.
- API shutdown stops new admission first, drains accepted HTTP work and streams, and then stops
  workers. If the drain deadline is reached, durable relay/outbox and Temporal state provide replay.

Run the deterministic recovery suite locally:

```bash
python -m pytest -q tests/chaos tests/test_capacity.py
```

Do not raise a limit to hide saturation. Identify the constrained pool, preserve the documented SLO
and safety margin, then change one capacity dimension at a time.
