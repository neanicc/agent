# Control API error reference

Every non-success response uses `application/problem+json` and includes a stable `code`, RFC 9457
`type`, title, safe detail, request ID, retryability, and documentation URL. Optional `field` and
`current_state` fields add safe corrective context. Include the request ID—not credentials,
request bodies, or tokens—when contacting support.

## LGAPI-ACTION-CONFLICT

- Meaning: the requested action transition conflicts with its durable lifecycle (`409`).
- Fix: fetch the action by ID and reconcile its current state before taking another step.
- Retry: do not create or re-sign a replacement until the existing action is reconciled.

## LGAPI-BODY-TOO-LARGE

- Meaning: the declared or streamed request body exceeded the endpoint limit (`413`).
- Fix: reduce the batch or use the documented artifact upload flow.
- Retry: only after reducing the body; reuse the same idempotency key for the same operation.

## LGAPI-CSRF-REQUIRED

- Meaning: a cookie-authenticated mutation lacked an allowed exact origin or matching CSRF proof
  (`403`).
- Fix: refresh the same-origin browser session and retry through the supported BFF.
- Retry: safe after obtaining a new CSRF token; do not replay a stale form automatically.

## LGAPI-DEVICE-PROOF-REQUIRED

- Meaning: the action lacks proof from a current, non-revoked registered device (`401`).
- Fix: repeat review with the registered device and sign the returned canonical challenge.
- Retry: use a fresh challenge; never reuse or silently substitute a device signature.

## LGAPI-FORBIDDEN

- Meaning: the authenticated principal lacks the required tenant permission (`403`).
- Fix: request the correct role from a tenant administrator or choose an allowed operation.
- Retry: not retryable until authorization changes.

## LGAPI-HOST-UNTRUSTED

- Meaning: the HTTP `Host` value is outside the deployment allowlist (`400`).
- Fix: use the configured API hostname or correct the ingress host rewrite.
- Retry: not retryable until routing is corrected.

## LGAPI-HOOK-BINDING

- Meaning: the signed hook named a repository outside its credential binding (`403`).
- Fix: install or rotate a credential specifically issued for that repository handle.
- Retry: not retryable with the rejected credential and repository combination.

## LGAPI-HOOK-INVALID

- Meaning: the hook key, timestamp, signature, body hash, or lifecycle state was invalid (`401`).
- Fix: refresh the secret configuration and sign the exact canonical request bytes.
- Retry: safe only as a newly signed request with a fresh nonce and valid credential.

## LGAPI-HOOK-REPLAY

- Meaning: the signed hook nonce was already consumed during its validity window (`409`).
- Fix: reconcile the original event ID; use a fresh nonce only for a genuinely new request.
- Retry: do not blindly retry the same signed request.

## LGAPI-INTERNAL

- Meaning: the service could not safely complete the request (`500`).
- Fix: retry with backoff and provide the request ID if the failure persists.
- Retry: retryable; preserve the original idempotency key for mutations.

## LGAPI-METHOD-NOT-ALLOWED

- Meaning: the resource does not implement that HTTP method (`405`).
- Fix: use the method documented in the endpoint contract.
- Retry: not retryable without changing the method.

## LGAPI-NOT-FOUND

- Meaning: the resource is absent or is not visible to the authenticated tenant (`404`).
- Fix: refresh the parent list and verify the opaque resource ID.
- Retry: not retryable without updated state. The response never reveals cross-tenant existence.

## LGAPI-ORIGIN-DENIED

- Meaning: a browser request supplied an origin outside the exact CORS allowlist (`403`).
- Fix: use an approved web application origin; wildcard subdomains are intentionally unsupported.
- Retry: not retryable from the denied origin.

## LGAPI-PAIRING-CONFLICT

- Meaning: the host pairing code is unknown or was already consumed (`409`).
- Fix: create a fresh one-time code from an authenticated owner/admin session.
- Retry: not retryable with the same code.

## LGAPI-PAIRING-EXPIRED

- Meaning: the five-minute host pairing window elapsed (`410`).
- Fix: create a fresh one-time pairing code and repeat local proof-of-possession.
- Retry: not retryable with the expired code.

## LGAPI-PROXY-UNTRUSTED

- Meaning: forwarded headers came from a peer outside the configured proxy networks (`400`).
- Fix: send the request directly without forwarded headers or correct the deployment proxy policy.
- Retry: not retryable until deployment routing is corrected.

## LGAPI-REQUEST-INVALID

- Meaning: one or more bounded request fields failed schema validation (`422`).
- Fix: correct the named `field` using the endpoint schema. Rejected values are never echoed.
- Retry: not retryable without changing the request.

## LGAPI-TIMEOUT

- Meaning: the request exceeded the service deadline (`504`).
- Fix: reconcile the operation by idempotency key before retrying with backoff.
- Retry: retryable, but acceptance or execution must not be inferred from the timeout.

## LGAPI-UNAUTHORIZED

- Meaning: authentication is missing, expired, or invalid (`401`).
- Fix: obtain a fresh token through the supported OIDC flow and preserve the request ID.
- Retry: safe only after reauthentication; do not loop on the same rejected credential.
