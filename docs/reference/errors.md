# Error reference

Every CLI, daemon, and API adapter error uses the same fields: `code`, `message`, `cause`, `suggested_commands`, `doc_url`, `request_id`, `retryable`, and redacted `details`. Human output prints the fix first. JSON output never includes internal tracebacks by default.

## LGD-DAEMON-001

The local daemon socket is missing or unreachable. Run `loopguard daemon start --foreground`, then `loopguard doctor`.

## LGD-STATE-002

The state directory or a state file is not owner-only. Inspect it with `loopguard doctor` and locate it with `loopguard config path`. LoopGuard fails closed and does not silently chmod an existing unsafe directory.

## LGD-SCHEMA-003

The database schema is older or newer than this LoopGuard build. Run `loopguard doctor`; starting a compatible daemon performs the supported forward migration after integrity checks.

## LGD-TRUST-004

An integration is waiting for explicit user trust. Review the exact hook scope, then run `loopguard setup --agent auto` when the managed integration command is available.

## LGD-CAP-005

The requested platform or lifecycle capability is not verified. Background start currently requires a managed service definition; use `loopguard daemon start --foreground`. Windows named-pipe support remains unadvertised until its native integration test passes.

## LGD-DEPS-006

An optional product dependency is missing. Install the control plane with `pip install "loopguard[control]"`.

## LGD-CONFIG-007

JSON, environment, or CLI configuration failed strict validation. Run `loopguard config validate` and `loopguard config show`.

## LGD-STORE-008

SQLite integrity, encrypted row metadata, or durable dispatch state failed verification. Do not delete or rewrite state blindly. Run `loopguard doctor` and preserve the state directory for diagnosis.

## Machine-readable example

```bash
loopguard explain LGD-DAEMON-001 --json
```

Use `request_id` to correlate safe local diagnostics. `details` are recursively redacted before serialization.
