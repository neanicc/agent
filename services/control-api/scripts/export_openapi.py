#!/usr/bin/env python3
"""Export the stable client contract and its generated reference.

The exporter checks the existing snapshot before replacing it. Removing an
operation, required response field, or enum member requires a major contract
version bump instead of an accidental client-breaking regeneration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from loopguard_api.app import create_app
from loopguard_api.errors import ERROR_CATALOG
from loopguard_api.settings import Settings


CONTRACT_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REFERENCE = ROOT / "docs/reference/control-api.md"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def build_contract(*, build_sha: str | None = None) -> dict[str, Any]:
    """Return a deterministic OpenAPI contract enriched with build metadata."""
    resolved_build = build_sha or os.environ.get("BUILD_SHA", "contract-test")
    schema = deepcopy(create_app(Settings.for_test()).openapi())
    schema["info"]["version"] = CONTRACT_VERSION
    schema["info"]["description"] = (
        "Versioned LoopGuard control-plane contract for web and native clients."
    )
    schema["x-loopguard-contract-version"] = CONTRACT_VERSION
    schema["x-loopguard-build-sha"] = resolved_build
    schema["servers"] = [{"url": "https://api.loopguard.example", "description": "Hosted API"}]
    return _sorted(schema)


def compatibility_breaks(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> list[str]:
    """Describe client-breaking response changes represented by OpenAPI."""
    breaks: list[str] = []
    old_paths = baseline.get("paths", {})
    new_paths = candidate.get("paths", {})
    for path, path_item in old_paths.items():
        for method in HTTP_METHODS:
            if method in path_item and method not in new_paths.get(path, {}):
                breaks.append(f"removed operation {method.upper()} {path}")

    old_schemas = baseline.get("components", {}).get("schemas", {})
    new_schemas = candidate.get("components", {}).get("schemas", {})
    for name, old_schema in old_schemas.items():
        new_schema = new_schemas.get(name)
        if not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
            continue
        old_required = set(old_schema.get("required", []))
        new_required = set(new_schema.get("required", []))
        for field in sorted(old_required - new_required):
            breaks.append(f"removed required response field {name}.{field}")
        old_properties = old_schema.get("properties", {})
        new_properties = new_schema.get("properties", {})
        for field, old_property in old_properties.items():
            if not isinstance(old_property, dict):
                continue
            old_enum = set(old_property.get("enum", []))
            new_property = new_properties.get(field, {})
            new_enum = set(new_property.get("enum", [])) if isinstance(new_property, dict) else set()
            for member in sorted(old_enum - new_enum, key=str):
                breaks.append(f"removed response enum member {name}.{field}={member}")
    return sorted(breaks)


def assert_compatible(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> None:
    old_version = _contract_version(baseline)
    new_version = _contract_version(candidate)
    if _major(old_version) != _major(new_version):
        return
    breaks = compatibility_breaks(baseline, candidate)
    if breaks:
        details = "\n".join(f"- {item}" for item in breaks)
        raise SystemExit(
            f"breaking OpenAPI changes require a major contract bump ({old_version} -> "
            f"{new_version}):\n{details}"
        )


def render_reference(schema: dict[str, Any]) -> str:
    """Render a deterministic human-readable reference from OpenAPI."""
    operations = list(_operations(schema))
    lines = [
        "# LoopGuard Control API",
        "",
        "> Generated from `contracts/control-api.openapi.json`. Do not edit by hand.",
        "",
        f"Contract version: `{schema['x-loopguard-contract-version']}`",
        f"Contract build: `{schema['x-loopguard-build-sha']}`",
        "",
        "## Authentication and request integrity",
        "",
        "User-facing `/v1` operations require `Authorization: Bearer <token>`. Tokens are validated "
        "against the configured OIDC issuer, audience, signature algorithm, expiry, tenant membership, "
        "role, and permission set. Browser code calls the same-origin web BFF and never receives the bearer token. "
        "Machine hook intake instead requires a scoped repository-bound HMAC credential, signed raw body, timestamp, "
        "and one-use nonce.",
        "",
        "Every request accepts `X-Request-ID`; the service generates a bounded opaque ID when it is "
        "missing or invalid and returns the effective value on every response. Cookie-authenticated "
        "state changes additionally require an exact allowed `Origin` and a matching `X-CSRF-Token`.",
        "",
        "## Permissions",
        "",
        "| Tag | Minimum permission |",
        "|---|---|",
        "| sessions, changes, verifications, repairs, capabilities, costs, audit | `session:view` |",
        "| action challenge/acceptance, artifact upload/completion | `session:control` |",
        "| preferences write | `policy:manage` |",
        "| device and pairing management | `device:manage` |",
        "| host pairing-code creation | `device:manage` |",
        "| enterprise domains, federation, tokens, and group mappings | `policy:manage` |",
        "| SCIM resources | Tenant-scoped SCIM bearer token |",
        "| billing summary, Checkout, and Portal | Tenant owner |",
        "",
        "Cross-tenant identifiers are intentionally indistinguishable from absent identifiers and return `404`.",
        "",
        "## Limits, cursors, and retries",
        "",
        "- Request bodies are capped at 1 MiB by default and request processing at 30 seconds; deployments may lower these limits.",
        "- Collection pages are bounded. `audit` accepts at most 200 records; opaque page cursors must only be reused with their owning endpoint.",
        "- Session replay uses durable `session_seq`; `client_stream_seq` is connection-local. Relay `local_log_seq` and cloud `cloud_ingest_seq` are separate domains.",
        "- Browser streams use one-use, origin-bound tickets that expire within 30 seconds. Access tokens never appear in URLs.",
        "- Reads may retry with bounded jitter. Action creation never retries silently; reconcile ambiguous outcomes with `GET /v1/actions/{action_id}`.",
        "- State-changing requests use their documented domain identifier or `Idempotency-Key`. HTTP `202` is acceptance, not execution success.",
        "",
        "## Operations",
        "",
        "| Method | Path | Operation ID | Authentication | Success responses |",
        "|---|---|---|---|---|",
    ]
    for method, path, operation in operations:
        responses = ", ".join(sorted(operation.get("responses", {})))
        if path in {"/v1/hook-events", "/v1/repair-intake"}:
            auth = "Signed hook"
        elif path == "/v1/billing/webhooks/stripe":
            auth = "Signed Stripe webhook"
        elif path.startswith("/scim/v2"):
            auth = "SCIM bearer"
        else:
            auth = "Bearer" if path.startswith("/v1") else "Public"
        lines.append(
            f"| `{method.upper()}` | `{path}` | `{operation['operationId']}` | {auth} | {responses} |"
        )

    lines.extend([
        "",
        "## Error contract",
        "",
        "Errors use `application/problem+json` and include stable `code`, `title`, `detail`, "
        "`request_id`, `retryable`, and `doc_url` fields. Validation failures may include `field`; "
        "state conflicts may include a bounded `current_state`.",
        "",
        "```json",
        json.dumps(
            {
                "type": "https://docs.loopguard.dev/reference/control-api-errors#lgapi-action-conflict",
                "code": "LGAPI-ACTION-CONFLICT",
                "title": "Action state conflict",
                "detail": "The action cannot transition from its current state.",
                "request_id": "req_01JEXAMPLE",
                "retryable": False,
                "doc_url": "https://docs.loopguard.dev/reference/control-api-errors#lgapi-action-conflict",
            },
            indent=2,
        ),
        "```",
        "",
        "### Stable error codes",
        "",
        "| Code | HTTP | Retryable | Meaning |",
        "|---|---:|---|---|",
    ])
    for code, definition in sorted(ERROR_CATALOG.items()):
        lines.append(
            f"| `{code}` | {definition.status} | {'yes' if definition.retryable else 'no'} | {definition.title} |"
        )

    lines.extend([
        "",
        "## Action-signature example",
        "",
        "The challenge response supplies canonical bytes. A registered device signs those exact bytes; "
        "the server verifies the device/user/action binding before countersigning for the host.",
        "",
        "```json",
        json.dumps(
            {
                "action_id": "act_01JEXAMPLE",
                "device_id": "018f0000-0000-7000-8000-000000000001",
                "device_key_id": "secure-enclave-key-01",
                "device_algorithm": "P-256",
                "device_signature": "<base64url-signature>",
            },
            indent=2,
        ),
        "```",
        "",
        "P-256 device signatures use DER-encoded ECDSA with SHA-256; the encrypted software fallback uses Ed25519. A stale state version, changed canonical hash, expired challenge, revoked device, replay, or invalid signature fails closed. Clients must fetch a fresh challenge and repeat explicit review.",
        "",
        "## Hook-signature example",
        "",
        "Host hooks authenticate a bounded raw request body with a repository-bound credential, timestamp, "
        "and one-use nonce. Exact header names and canonicalization are defined by the installed adapter contract.",
        "",
        "```text",
        "X-LoopGuard-Key-ID: hk_01JEXAMPLE",
        "X-LoopGuard-Repository: rh_01JEXAMPLE",
        "X-LoopGuard-Timestamp: 2026-07-30T12:00:00+00:00",
        "X-LoopGuard-Nonce: 01JEXAMPLE",
        "X-LoopGuard-Signature: <hex-hmac-sha256>",
        "```",
        "",
        "`POST /v1/repair-intake` additionally requires the credential's `repair:intake` scope. "
        "The source value inside JSON is untrusted event data and never establishes identity.",
        "",
        "## Client fixtures",
        "",
        "Deterministic fixtures live in `contracts/fixtures/` for stream replay/gaps, action lifecycle "
        "states, effective capabilities, and host/integration health. Fixtures are non-sensitive examples "
        "and are the shared behavioral vocabulary for web, iOS, and compatibility tests.",
        "",
    ])
    return "\n".join(lines)


def export(output: Path, reference: Path, *, build_sha: str | None = None) -> None:
    candidate = build_contract(build_sha=build_sha)
    if output.exists():
        assert_compatible(json.loads(output.read_text()), candidate)
    output.parent.mkdir(parents=True, exist_ok=True)
    reference.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
    reference.write_text(render_reference(candidate))


def _operations(schema: dict[str, Any]) -> Iterable[tuple[str, str, dict[str, Any]]]:
    for path in sorted(schema.get("paths", {})):
        for method in HTTP_METHODS:
            operation = schema["paths"][path].get(method)
            if operation is not None:
                yield method, path, operation


def _contract_version(schema: dict[str, Any]) -> str:
    return str(schema.get("x-loopguard-contract-version") or schema.get("info", {}).get("version", "0.0.0"))


def _major(version: str) -> int:
    match = re.fullmatch(r"(\d+)\.\d+\.\d+", version)
    if match is None:
        raise SystemExit(f"invalid semantic contract version: {version}")
    return int(match.group(1))


def _sorted(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sorted(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sorted(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="OpenAPI JSON output path")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--build-sha", default=None)
    args = parser.parse_args()
    export(args.output.resolve(), args.reference.resolve(), build_sha=args.build_sha)


if __name__ == "__main__":
    main()
