# LoopGuard control API endpoint contract

All paths are versioned under `/v1`, require a verified tenant principal unless explicitly marked
as a host/hook exchange, and return the documented RFC 9457 problem union. List operations are
bounded and stable; cross-tenant detail IDs return the same `404` as absent IDs.

| Operation | Permission | Idempotency / transition | Cursor domain | Owner |
|---|---|---|---|---|
| `GET /v1/capabilities` | `session:view` | Read-only effective intersection | Observed-at + TTL | CLOUD-T09 |
| `GET /v1/hosts` | `session:view` | Read-only | Opaque `(created_at,id)` | CLOUD-T09 |
| `GET /v1/hosts/{host_id}` | `session:view` | Read-only | Resource ID | CLOUD-T09 |
| `GET /v1/sessions` | `session:view` | Read-only | Opaque `(created_at,id)` | CLOUD-T09 |
| `GET /v1/sessions/{session_id}` | `session:view` | Read-only | Resource ID | CLOUD-T09 |
| `GET /v1/changes` | `session:view` | Read-only | Opaque `(created_at,id)` | CLOUD-T09 |
| `GET /v1/changes/{change_id}` | `session:view` | Read-only | Resource ID | CLOUD-T09 |
| `GET /v1/verifications` | `session:view` | Read-only | Opaque `(created_at,id)` | CLOUD-T09 |
| `GET /v1/verifications/{verification_id}` | `session:view` | Read-only | Resource ID | CLOUD-T09 |
| `GET /v1/actions` | `session:view` | Read-only | Opaque `(created_at,id)` | CLOUD-T09 |
| `GET /v1/actions/{action_id}` | `session:view` | Reconciliation read | Resource ID | CLOUD-T09 |
| `POST /v1/actions/challenge` | `session:control` | New one-use challenge | Action ID | CLOUD-T06 |
| `POST /v1/actions` | `session:control` | Action ID; reviewed → queued | Action ID | CLOUD-T06 |
| `GET /v1/repairs` | `session:view` | Empty until Auto-Heal enables workflow | Opaque `(created_at,id)` | CLOUD-T09 |
| `GET /v1/repairs/{repair_id}` | `session:view` | Read-only; feature fails closed | Resource ID | CLOUD-T09 |
| `POST /v1/artifacts` | `session:control` | New upload authorization | Artifact ID | CLOUD-T07 |
| `POST /v1/artifacts/{artifact_id}/complete` | `session:control` | Idempotent completion | Artifact ID | CLOUD-T07 |
| `GET /v1/artifacts/{artifact_id}` | `session:view` | Metadata only | Resource ID | CLOUD-T07 |
| `GET /v1/artifacts/{artifact_id}/download` | `session:view` | Audited short-lived URL | Resource ID | CLOUD-T07 |
| `GET /v1/preferences` | `session:view` | Read-only | Profile version | CLOUD-T09 |
| `PUT /v1/preferences` | `policy:manage` | New profile version | Profile version | CLOUD-T09 |
| `GET /v1/costs` | `session:view` | Read-only observed totals | Fixed time window | CLOUD-T09 |
| `GET /v1/devices` | `session:view` | Read-only | Opaque `(created_at,id)` | CLOUD-T09 |
| `POST /v1/devices/pairing/start` | `device:manage` | New five-minute challenge | Pairing ID | CLOUD-T09 |
| `POST /v1/devices/pairing/complete` | `device:manage` | Pairing ID, one-use | Device ID | CLOUD-T09 |
| `DELETE /v1/devices/{device_id}` | `device:manage` | Idempotent revocation by device ID | Device ID | CLOUD-T09 |
| `POST /v1/stream-tickets` | `session:view` + CSRF for cookies | New 30-second one-use ticket | `session_seq` | CLOUD-T05 |
| `GET /v1/audit` | `session:view` | Read-only append-only history | Opaque `(created_at,id)` | CLOUD-T09 |

The WebSocket `GET /v1/sessions/{session_id}/stream` replays by durable `session_seq` and assigns
a distinct connection-local `client_stream_seq`. Browser upgrades carry only a one-use ticket;
native upgrades carry a bearer header. Relay upload cursors remain `local_log_seq`, while global
cloud ingestion uses `cloud_ingest_seq`; no cursor domain is interchangeable with another.

Writes use `Idempotency-Key` where the table names an existing domain ID. Action HTTP acceptance
is never an execution receipt. Repair schemas intentionally exist before Auto-Heal population, but
capabilities continue to report `feature_flag_disabled` until that owning workflow is registered.
