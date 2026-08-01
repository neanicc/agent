# Billing and entitlement safety

LoopGuard’s billing boundary stores Stripe customer/subscription/event IDs, entitlement state,
catalog versions, invoices, and append-only adjustments. It never receives or stores card numbers,
bank details, or payment-method payloads.

## Stripe setup

Production injects a durable billing service and resolves its API key and webhook secret from the
deployment secret manager. The concrete adapter creates Stripe Checkout and Customer Portal
sessions with:

- a pre-bound `cus_...` customer;
- one configured `price_...` subscription price;
- exact HTTPS return origins from the application allowlist;
- form fields containing no card or payment-method data.

Configure the webhook endpoint as:

```text
POST https://api.example.com/v1/billing/webhooks/stripe
```

The handler verifies Stripe-style `t=...,v1=...` HMAC over the exact raw body, permits at most five
minutes of clock skew, binds the provider customer to a server-side tenant, and deduplicates
`evt_...` IDs. Provider creation time orders entitlement changes; an older event is recorded as
seen but cannot roll back newer state.

## Entitlement states

| State | New hosted expensive work | Local guarding / read / export / delete / security |
| --- | --- | --- |
| active | allowed within quota | always available |
| grace | allowed within quota until `grace_until` | always available |
| blocked | rejected before work starts | always available |
| deleted | rejected | local product remains independent |

`invoice.payment_failed` starts the configured grace period. A later valid paid/active event clears
grace. Expired grace, paused subscription, or deleted subscription blocks only new hosted expensive
work. It never deletes evidence or interrupts local LoopGuard protection.

## Prices, invoices, credits, and refunds

Price catalogs are immutable and versioned. Each usage entry keeps its catalog version, so a price
change mid-period produces separate invoice lines at the old and new prices. USD lines use decimal
arithmetic and round half-up to cents; binary floating point is forbidden.

Invoices reconcile by rebuilding their lines from the immutable usage ledger. The expected total,
provider/customer invoice total, and difference are retained. A difference other than `$0.00`
blocks finalization. Credits, refunds, and manual corrections are new signed/audited adjustment
records with stable IDs, amounts, reasons, and kinds; existing lines are not edited.

## Failure handling

- Forged, stale, malformed, unbound, and replayed webhooks fail closed.
- Provider outages return a stable retryable error without changing subscription state.
- An ambiguous Checkout/Portal response must be reconciled through Stripe before a user retries a
  payment operation.
- Webhook secrets and API keys are never logged, returned, stored in the price catalog, or placed in
  client code.
- Daily jobs reconcile event ordering, provider subscription state, usage totals, and invoices.

Production alerts on invalid-signature bursts, webhook lag over five minutes, grace transitions,
reconciliation differences, unknown catalogs, quota conflict rates, and provider failures. Billing
operators can inspect non-sensitive provider IDs and request IDs but cannot access tenant evidence.
