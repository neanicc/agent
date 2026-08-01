# Hosted pricing and limits

LoopGuard’s local circuit breaker is independent from hosted billing. A hosted payment failure,
quota, or provider outage never disables local guarding, data export/deletion, security actions, or
read-only access.

## Current commercial status

Public hosted sales and billing activation are **NO-GO**. No price, free tier, trial, SLA, or
purchase promise is approved by this repository. Activation requires product and finance owners to
approve a versioned catalog, legal terms and privacy notices, support coverage, tax/provider setup,
and a reconciled staging invoice. Distribution also remains
`awaiting_owner_legal_choice` because the repository has no license.

## Transparent billing dimensions

If hosted billing is activated, the immutable catalog can price only these explicit units:

| Dimension | Unit |
| --- | --- |
| Hosted storage | byte-hour |
| Retained cloud events | event |
| Managed compute | second |
| Judge provider usage | USD of upstream cost |
| Visual critic provider usage | USD of upstream cost |
| Hosted browser | minute |
| Repair worker | second |

Upstream provider cost and LoopGuard’s customer charge are separate fields. Avoided-cost estimates
are explanatory and are never billable usage. Catalog versions are immutable, decimal charges
round to cents, and invoice finalization fails on any reconciliation difference.

## Safety limits

Bootstrap production defaults include 1 MiB request bodies, 100-event ingest batches, 5 GiB
artifact declarations, 60 action challenges per tenant per minute, 10 active repair workflows per
tenant, and 20 live session streams per tenant. These are safety limits, not promised plan
entitlements or measured product capacity.

Expensive hosted work reserves quota before any side effect. The reservation commits exact usage
after durable completion and releases on cancellation or failure. Exhaustion returns a structured
error with retry guidance; accepted work is never silently dropped.

See [metering](../operations/metering.md), [billing](../operations/billing.md), and
[capacity](../operations/capacity.md) for the operational contracts.
