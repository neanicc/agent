# Cloud retention and deletion

LoopGuard applies retention by data class, never by a blanket table purge. Legal or contractual
holds override every ordinary expiry and every hold transition is appended to audit.

| Data class | Ordinary retention | Tenant deletion | Mutation authority |
|---|---|---|---|
| Events | Tenant-configured window | Delete | Privileged retention workflow |
| Source and log artifacts | 30 days by default | Crypto-shred and delete object | Privileged retention workflow |
| Verification proofs | Contract term | Retain or crypto-shred per contract | Privileged retention workflow |
| Repair artifacts | Contract term | Retain or crypto-shred per contract | Privileged retention workflow |
| User and device metadata | Account lifetime | De-identify | Privileged retention workflow |
| Billing records | Statutory period | Retain | Append tombstone only |
| Audit | Contractual/statutory period | De-identify allowed personal fields | Append tombstone only |

The ordinary application role cannot update or delete audit rows. PostgreSQL enforces this with
revoked privileges and a mutation-rejecting trigger, while the ORM also fails before flush. The
separate human-authorized retention workflow may de-identify the explicitly allowed personal
fields or crypto-shred artifact keys, and must append a tombstone describing its authority and
result. It never makes a retained audit identity look like an active user or device.

Temporal workflow IDs come from domain IDs (`notify-action/{id}`, `expire-action/{id}`,
`delete-artifact/{id}`, `verification/{id}`, and `repair/{id}`). Activities use matching
idempotency keys, bounded retry, and pass only opaque IDs—never secrets—through workflow history.
