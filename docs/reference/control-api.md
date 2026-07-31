# LoopGuard Control API

> Generated from `contracts/control-api.openapi.json`. Do not edit by hand.

Contract version: `1.0.0`  
Contract build: `contract-test`

## Authentication and request integrity

User-facing `/v1` operations require `Authorization: Bearer <token>`. Tokens are validated against the configured OIDC issuer, audience, signature algorithm, expiry, tenant membership, role, and permission set. Browser code calls the same-origin web BFF and never receives the bearer token. Machine hook intake instead requires a scoped repository-bound HMAC credential, signed raw body, timestamp, and one-use nonce.

Every request accepts `X-Request-ID`; the service generates a bounded opaque ID when it is missing or invalid and returns the effective value on every response. Cookie-authenticated state changes additionally require an exact allowed `Origin` and a matching `X-CSRF-Token`.

## Permissions

| Tag | Minimum permission |
|---|---|
| sessions, changes, verifications, repairs, capabilities, costs, audit | `session:view` |
| action challenge/acceptance, artifact upload/completion | `session:control` |
| preferences write | `policy:manage` |
| device and pairing management | `device:manage` |
| host pairing-code creation | `device:manage` |

Cross-tenant identifiers are intentionally indistinguishable from absent identifiers and return `404`.

## Limits, cursors, and retries

- Request bodies are capped at 1 MiB by default and request processing at 30 seconds; deployments may lower these limits.
- Collection pages are bounded. `audit` accepts at most 200 records; opaque page cursors must only be reused with their owning endpoint.
- Session replay uses durable `session_seq`; `client_stream_seq` is connection-local. Relay `local_log_seq` and cloud `cloud_ingest_seq` are separate domains.
- Browser streams use one-use, origin-bound tickets that expire within 30 seconds. Access tokens never appear in URLs.
- Reads may retry with bounded jitter. Action creation never retries silently; reconcile ambiguous outcomes with `GET /v1/actions/{action_id}`.
- State-changing requests use their documented domain identifier or `Idempotency-Key`. HTTP `202` is acceptance, not execution success.

## Operations

| Method | Path | Operation ID | Authentication | Success responses |
|---|---|---|---|---|
| `GET` | `/health` | `health_health_get` | Public | 200 |
| `GET` | `/v1/actions` | `list_actions_v1_actions_get` | Bearer | 200 |
| `POST` | `/v1/actions` | `accept_signed_action_v1_actions_post` | Bearer | 202, 422 |
| `POST` | `/v1/actions/challenge` | `create_action_challenge_v1_actions_challenge_post` | Bearer | 201, 422 |
| `GET` | `/v1/actions/{action_id}` | `read_action_v1_actions__action_id__get` | Bearer | 200, 422 |
| `POST` | `/v1/artifacts` | `initiate_artifact_v1_artifacts_post` | Bearer | 201, 422 |
| `GET` | `/v1/artifacts/{artifact_id}` | `read_artifact_v1_artifacts__artifact_id__get` | Bearer | 200, 422 |
| `POST` | `/v1/artifacts/{artifact_id}/complete` | `complete_artifact_v1_artifacts__artifact_id__complete_post` | Bearer | 200, 422 |
| `GET` | `/v1/artifacts/{artifact_id}/download` | `download_artifact_v1_artifacts__artifact_id__download_get` | Bearer | 200, 422 |
| `GET` | `/v1/audit` | `list_audit_v1_audit_get` | Bearer | 200, 422 |
| `GET` | `/v1/capabilities` | `effective_capabilities_v1_capabilities_get` | Bearer | 200, 422 |
| `GET` | `/v1/changes` | `list_changes_v1_changes_get` | Bearer | 200 |
| `GET` | `/v1/changes/{change_id}` | `read_change_v1_changes__change_id__get` | Bearer | 200, 422 |
| `GET` | `/v1/costs` | `read_costs_v1_costs_get` | Bearer | 200, 422 |
| `GET` | `/v1/devices` | `list_devices_v1_devices_get` | Bearer | 200 |
| `POST` | `/v1/devices/pairing/complete` | `complete_device_pairing_v1_devices_pairing_complete_post` | Bearer | 201, 422 |
| `POST` | `/v1/devices/pairing/start` | `start_device_pairing_v1_devices_pairing_start_post` | Bearer | 200 |
| `DELETE` | `/v1/devices/{device_id}` | `revoke_device_v1_devices__device_id__delete` | Bearer | 204, 422 |
| `PUT` | `/v1/devices/{device_id}/push-token` | `register_push_destination_v1_devices__device_id__push_token_put` | Bearer | 200, 422 |
| `POST` | `/v1/hook-events` | `ingest_hook_event_v1_hook_events_post` | Signed hook | 202, 422 |
| `GET` | `/v1/hosts` | `list_hosts_v1_hosts_get` | Bearer | 200 |
| `POST` | `/v1/hosts/pair` | `pair_host_v1_hosts_pair_post` | Bearer | 201, 422 |
| `POST` | `/v1/hosts/pairing-codes` | `create_pairing_code_v1_hosts_pairing_codes_post` | Bearer | 200 |
| `GET` | `/v1/hosts/{host_id}` | `read_host_v1_hosts__host_id__get` | Bearer | 200, 422 |
| `GET` | `/v1/me` | `me_v1_me_get` | Bearer | 200 |
| `GET` | `/v1/preferences` | `read_preferences_v1_preferences_get` | Bearer | 200 |
| `PUT` | `/v1/preferences` | `write_preferences_v1_preferences_put` | Bearer | 200, 422 |
| `POST` | `/v1/repair-intake` | `ingest_repair_failure_v1_repair_intake_post` | Signed hook | 202, 422 |
| `GET` | `/v1/repairs` | `list_repairs_v1_repairs_get` | Bearer | 200, 422 |
| `GET` | `/v1/repairs/{repair_id}` | `read_repair_v1_repairs__repair_id__get` | Bearer | 200, 422 |
| `GET` | `/v1/sessions` | `list_sessions_v1_sessions_get` | Bearer | 200 |
| `GET` | `/v1/sessions/{session_id}` | `read_session_v1_sessions__session_id__get` | Bearer | 200, 422 |
| `POST` | `/v1/stream-tickets` | `create_stream_ticket_v1_stream_tickets_post` | Bearer | 201, 422 |
| `GET` | `/v1/verifications` | `list_verifications_v1_verifications_get` | Bearer | 200 |
| `GET` | `/v1/verifications/{verification_id}` | `read_verification_v1_verifications__verification_id__get` | Bearer | 200, 422 |

## Error contract

Errors use `application/problem+json` and include stable `code`, `title`, `detail`, `request_id`, `retryable`, and `doc_url` fields. Validation failures may include `field`; state conflicts may include a bounded `current_state`.

```json
{
  "type": "https://docs.loopguard.dev/reference/control-api-errors#lgapi-action-conflict",
  "code": "LGAPI-ACTION-CONFLICT",
  "title": "Action state conflict",
  "detail": "The action cannot transition from its current state.",
  "request_id": "req_01JEXAMPLE",
  "retryable": false,
  "doc_url": "https://docs.loopguard.dev/reference/control-api-errors#lgapi-action-conflict"
}
```

### Stable error codes

| Code | HTTP | Retryable | Meaning |
|---|---:|---|---|
| `LGAPI-ACTION-CONFLICT` | 409 | no | Action state conflict |
| `LGAPI-ARTIFACT-INTEGRITY` | 422 | no | Artifact integrity check failed |
| `LGAPI-BODY-TOO-LARGE` | 413 | no | Request body too large |
| `LGAPI-CSRF-REQUIRED` | 403 | no | CSRF proof required |
| `LGAPI-DEVICE-PAIRING-CONFLICT` | 409 | no | Device pairing conflict |
| `LGAPI-DEVICE-PROOF-REQUIRED` | 401 | no | Device proof required |
| `LGAPI-FORBIDDEN` | 403 | no | Operation forbidden |
| `LGAPI-HOOK-BINDING` | 403 | no | Hook repository denied |
| `LGAPI-HOOK-INVALID` | 401 | no | Hook authentication failed |
| `LGAPI-HOOK-REPLAY` | 409 | no | Hook replay rejected |
| `LGAPI-HOST-UNTRUSTED` | 400 | no | Untrusted host |
| `LGAPI-INTERNAL` | 500 | yes | Internal service error |
| `LGAPI-MANAGED-RULE-WEAKENED` | 422 | no | Managed rule cannot be weakened |
| `LGAPI-METHOD-NOT-ALLOWED` | 405 | no | Method not allowed |
| `LGAPI-NOT-FOUND` | 404 | no | Resource not found |
| `LGAPI-ORIGIN-DENIED` | 403 | no | Origin denied |
| `LGAPI-PAIRING-CONFLICT` | 409 | no | Pairing code unavailable |
| `LGAPI-PAIRING-EXPIRED` | 410 | no | Pairing code expired |
| `LGAPI-PROXY-UNTRUSTED` | 400 | no | Untrusted proxy headers |
| `LGAPI-REQUEST-INVALID` | 422 | no | Request validation failed |
| `LGAPI-TIMEOUT` | 504 | yes | Request timed out |
| `LGAPI-UNAUTHORIZED` | 401 | no | Authentication required |

## Action-signature example

The challenge response supplies canonical bytes. A registered device signs those exact bytes; the server verifies the device/user/action binding before countersigning for the host.

```json
{
  "action_id": "act_01JEXAMPLE",
  "device_id": "018f0000-0000-7000-8000-000000000001",
  "device_key_id": "secure-enclave-key-01",
  "device_algorithm": "P-256",
  "device_signature": "<base64url-signature>"
}
```

P-256 device signatures use DER-encoded ECDSA with SHA-256; the encrypted software fallback uses Ed25519. A stale state version, changed canonical hash, expired challenge, revoked device, replay, or invalid signature fails closed. Clients must fetch a fresh challenge and repeat explicit review.

## Hook-signature example

Host hooks authenticate a bounded raw request body with a repository-bound credential, timestamp, and one-use nonce. Exact header names and canonicalization are defined by the installed adapter contract.

```text
X-LoopGuard-Key-ID: hk_01JEXAMPLE
X-LoopGuard-Repository: rh_01JEXAMPLE
X-LoopGuard-Timestamp: 2026-07-30T12:00:00+00:00
X-LoopGuard-Nonce: 01JEXAMPLE
X-LoopGuard-Signature: <hex-hmac-sha256>
```

`POST /v1/repair-intake` additionally requires the credential's `repair:intake` scope. The source value inside JSON is untrusted event data and never establishes identity.

## Client fixtures

Deterministic fixtures live in `contracts/fixtures/` for stream replay/gaps, action lifecycle states, effective capabilities, and host/integration health. Fixtures are non-sensitive examples and are the shared behavioral vocabulary for web, iOS, and compatibility tests.
