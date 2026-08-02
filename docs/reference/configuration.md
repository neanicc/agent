# Configuration reference

LoopGuard resolves configuration in this order, from lowest to highest precedence:

1. safe built-in defaults;
2. `$LOOPGUARD_HOME/config.json`;
3. `LOOPGUARD_*` environment variables;
4. command-line overrides where a command exposes them.

Inspect the exact effective values and their sources:

```bash
loopguard config path
loopguard config show --json
loopguard config validate
```

Output is recursively redacted. LoopGuard never intentionally prints tokens, passwords, authorization values, or provider secrets.

## File shape

```json
{
  "schema_version": 1,
  "daemon": {
    "max_frame_bytes": 1048576,
    "max_concurrent_clients": 32,
    "idle_timeout_seconds": 30,
    "write_timeout_seconds": 5,
    "handler_queue_size": 128,
    "handler_max_attempts": 3,
    "handler_retry_delay_seconds": 0.05
  },
  "guard": {
    "trip_count": 3,
    "action": "pause"
  },
  "telemetry": {
    "enabled": false
  }
}
```

Unspecified fields retain defaults. Unknown top-level, daemon, telemetry, or schema-version fields fail validation instead of being silently accepted.

## Environment variables

| Variable | Setting |
|---|---|
| `LOOPGUARD_HOME` | State and configuration directory |
| `LOOPGUARD_MAX_FRAME_BYTES` | `daemon.max_frame_bytes` |
| `LOOPGUARD_MAX_CONCURRENT_CLIENTS` | `daemon.max_concurrent_clients` |
| `LOOPGUARD_IDLE_TIMEOUT_SECONDS` | `daemon.idle_timeout_seconds` |
| `LOOPGUARD_WRITE_TIMEOUT_SECONDS` | `daemon.write_timeout_seconds` |
| `LOOPGUARD_HANDLER_QUEUE_SIZE` | `daemon.handler_queue_size` |
| `LOOPGUARD_HANDLER_MAX_ATTEMPTS` | `daemon.handler_max_attempts` |
| `LOOPGUARD_HANDLER_RETRY_DELAY_SECONDS` | `daemon.handler_retry_delay_seconds` |
| `LOOPGUARD_TRIP_COUNT` | `guard.trip_count` |
| `LOOPGUARD_ACTION` | `guard.action` |
| `LOOPGUARD_TELEMETRY_ENABLED` | `telemetry.enabled`; defaults to `false` |

Local state defaults to `~/.loopguard`. On POSIX, the directory must be mode `0700`; the database, socket, and PID file must be owner-only.
