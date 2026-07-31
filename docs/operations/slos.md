# Production service-level objectives

Window: rolling 30 days unless noted  
Paging owner: Production Operations  
Product owner: Control Plane  
Measurement source: server/worker metrics plus synthetic probes; never client-only analytics

| Capability | SLI | Objective | Error budget | Alert owner |
|---|---|---:|---:|---|
| Local event durability | Successfully committed and decryptable events / accepted local events | 99.99% | 4.32 min/month equivalent | Local Runtime |
| Cloud ingest availability | Non-overload valid batches durably accepted / valid attempts | 99.95% | 21.6 min/month | Control Plane |
| Relay latency | Durable local event to durable cloud acknowledgement | 99% under 5s; p99 under 15s | 1% slow | Relay |
| Action resolution | Signed accepted actions reach an explicit terminal/reconciling state inside 60s | 99.9% | 43.2 min/action-minutes | Actions |
| Stream replay | Reconnects restore an ordered cursor without gap or duplication | 99.99%; p99 under 2s for 10k events | 0.01% failed | Streaming |
| Verification completion | Eligible deterministic verification completes inside 10 min | 99% | 1% slow/failed | Verification |
| Repair workflow availability | Eligible workflow reaches evidence, explicit failure, or approval wait inside 30 min | 99% | 1% slow/failed | Auto-Heal |
| Control API availability | Authenticated non-overload requests return non-5xx | 99.95% | 21.6 min/month | Control Plane |

Overload rejected before acceptance with an explicit retry response is reported separately from
availability and counts against capacity SLOs when it occurs below the documented supported load.
Invalid authentication, authorization, signature, stale state, quota, and tenant-boundary
rejections are not server errors.

## Burn-rate alerts

- Page on-call when both the 5-minute burn exceeds 14.4× and the 1-hour burn exceeds 6×.
- Page during business/on-call coverage when both the 30-minute burn exceeds 6× and the 6-hour
  burn exceeds 3×.
- Create a ticket when 3-day burn exceeds 1× or 30-day remaining budget drops below 25%.
- Any cross-tenant result, signature bypass, accepted-and-lost event/action, or corrupted restore
  is a security incident regardless of aggregate SLO.

Alerts include service, SLO, window, observed value, request/workflow correlation IDs, deployment
digest, runbook, and dashboard. They never include prompts, source, tool arguments/output,
credentials, raw artifact content, push tokens, or payment data.

## Correlation and sampling

Request, event, session, action, and workflow IDs propagate as span attributes. IDs are allowed on
traces/logs for investigation but never metric labels. Metrics use bounded route templates,
method, status class, workflow type, category, and outcome only. Errors are sampled at 100%;
normal high-volume spans may be tail-sampled while SLI counters remain unsampled.

Local telemetry is opt-in and disabled by default. Hosted service telemetry is enabled by the
deployment configuration and exported over authenticated OTLP to the private collector endpoint.
