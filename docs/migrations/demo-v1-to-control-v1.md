# Migrate demo/prototype state to the control-v1 product

This guide moves a local installation from the demo-era state into the encrypted control-v1 local
store. It does not upload source, prompts, credentials, or old in-memory server state.

## Before you start

1. Stop `loopguard daemon` and close the Expo prototype.
2. Install the target LoopGuard version using a pinned, verified artifact.
3. Ensure the OS credential service is available: Keychain on macOS, DPAPI on Windows, or
   libsecret on Linux. The encrypted-file fallback prints an explicit warning and is not the
   preferred production configuration.
4. Keep at least three times the `events.db` size free on the destination volume.

The original demo server kept runs, auto-fixes, and allowlists in memory. Restarted or already-lost
in-memory data cannot be migrated. Do not invent records from screenshots or logs.

## Preview

```bash
loopguard migrate check --json
loopguard doctor --json
```

`migration_required` lists every forward step. `current` means no write is needed.
`unsafe_permissions`, `integrity_failed`, or `newer_schema` is a hard stop.

## Apply

```bash
loopguard migrate apply --yes --json
```

The command snapshots SQLite (including committed WAL content), encrypts the snapshot with a key
stored in the platform credential service, writes it mode `0600`, decrypts it in memory to verify
the backup, checks free space, applies forward-only migrations, and runs `PRAGMA quick_check`.
It prints the exact backup path and restore command. Re-running after success is safe and reports
`current`.

Prototype `.jsonl` demo events should be retained offline as an audit reference, not merged into
the authoritative encrypted store unless a separately versioned importer validates their schema,
redacts payloads, and assigns conflict-safe immutable IDs.

## Verify

```bash
loopguard doctor --json
loopguard sessions --json
loopguard migrate check --json
```

Confirm that schema status is current, the owner-only event store opens, session ordering is
intact, and integrations are trusted. Pair the host and devices again; prototype tokens and
allowlists are not production credentials or policies.

## Restore

If migration reports `migration_failed`, keep the daemon stopped and use the exact printed command:

```bash
loopguard migrate restore --backup /path/events.db.TIMESTAMP.lgbak \
  --database /path/events.db --yes
```

Restore verifies file permissions, authenticated encryption, database name binding, and SQLite
integrity before atomically replacing the database. Run `loopguard doctor --json` afterward.
Never downgrade by editing `PRAGMA user_version` or copying tables between schemas.
