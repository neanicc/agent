# Hosted usage metering

LoopGuard meters hosted resources through an append-only, tenant-scoped ledger. Local guarding is
never metered and never stops because a hosted quota or payment state is exhausted.

## Metered units

| Category | Unit | Recorded by |
| --- | --- | --- |
| `hosted_storage_byte_hours` | one stored byte for one hour | storage inventory worker |
| `retained_events` | one retained cloud event | relay retention worker |
| `managed_compute_seconds` | one hosted compute second | managed worker |
| `judge_usd` | one provider-cost USD | judge usage reconciler |
| `critic_usd` | one provider-cost USD | visual critic reconciler |
| `browser_minutes` | one hosted browser minute | browser worker |
| `repair_worker_seconds` | one repair worker second | repair workflow activities |

Every entry has a tenant-bound `usage_id`, category, finite positive units, source, observation time,
provider cost where applicable, and the exact price-catalog version active when the usage occurred.
Replaying the same ID with identical semantics is a no-op. Reusing it with different units,
category, source, cost, time, or catalog fails and alerts.

Provider cost and LoopGuard charges are separate:

- `provider_cost_usd` is the upstream amount observed from a model/browser/compute provider;
- the customer charge is computed later from immutable units and the versioned catalog;
- avoided-cost estimates are explanatory and are never billed usage.

## Reservation flow

Expensive hosted work uses a reserve/commit/release transaction:

1. atomically reserve estimated units before starting;
2. reject before side effects if committed plus reserved units exceed quota;
3. commit exact usage once durable work finishes;
4. release on cancellation, timeout, provider failure, or admission rollback.

Reservation IDs and usage IDs are idempotent. Durable hosted adapters implement this transaction in
PostgreSQL; the in-memory adapter exists only for local/test composition. Repair admission also
checks billing grace before workflow capacity, so a rejected repair is never accepted or silently
dropped.

The following operation classes bypass hosted quota and create no hosted charge:

- local guard decisions;
- remote reads;
- data export and deletion;
- security and credential-revocation actions.

## Operations and reconciliation

Monitor duplicate conflicts, reservation age, committed-versus-provider usage, missing catalog
versions, per-category lag, and ledger-to-invoice difference. A reservation older than its worker
deadline is investigated and safely released only after reconciling the durable job ID.

Daily reconciliation compares provider usage to the immutable ledger. Monthly reconciliation
rebuilds every invoice line from ledger units and catalog version. A non-zero difference blocks
invoice finalization. Corrections are new adjustment records; operators never edit or delete usage.

Tenant deletion removes provider bindings and anonymizes ledger ownership with a keyed tombstone
when financial retention is legally required. The original tenant identifier must no longer return
entries. Retention expiration is handled by the privacy workflow and leaves only payload-free audit
evidence.
