# Disaster recovery

LoopGuard recovery is fail-closed: a restore is not usable until the PostgreSQL dump, object
inventory, stream positions, artifact hashes, and resolved actions match the encrypted backup.

## Targets

- Hosted production target: **RPO 5 minutes**, driven by continuous database/object replication.
- Regional restore target: **RTO 60 minutes** after an incident commander authorizes recovery.
- Backup bundles are a second recovery path, retained under the same legal-hold and deletion rules
  as the source tenant data.

These are objectives, not claims. A release may advertise them only after the quarterly isolated
restore and regional exercise records timestamps that meet both budgets.

## Create an encrypted recovery bundle

Requirements: PostgreSQL client tools matching the server major version, `age`, an object inventory
export with object key/SHA-256/byte count, and a hardware- or cloud-KMS-controlled age recipient.

```bash
cd services/control-api
export LOOPGUARD_API_DATABASE_URL='postgresql://...'
export LOOPGUARD_OBJECT_MANIFEST=/secure/export/object-manifest.json
export LOOPGUARD_BACKUP_AGE_RECIPIENT='age1...'
scripts/backup.sh /secure/backups/loopguard-2026-07-30.age
```

The script uses `pg_dump` custom format, captures integrity invariants, validates the object
manifest, hashes every component, packages only four allowlisted files, encrypts before publishing,
and writes with owner-only permissions. Never place database credentials or age identities in the
repository, shell history, artifact logs, or CI output.

## Restore and prove integrity

Create an empty, network-isolated database and use a short-lived restore role. The script refuses a
target URL equal to the configured source URL.

```bash
export LOOPGUARD_RESTORE_DATABASE_URL='postgresql://.../loopguard_restore'
export LOOPGUARD_BACKUP_AGE_IDENTITY=/secure/keys/loopguard-backup.agekey
scripts/restore.sh \
  /secure/backups/loopguard-2026-07-30.age \
  /secure/restore-evidence/2026-07-30
```

Before `pg_restore`, extraction rejects absolute paths, traversal, links, extra members, missing
members, and checksum mismatches. After restore, the script compares:

- maximum durable cloud ingest sequence;
- maximum replay position for every session;
- SHA-256 for every complete artifact record;
- every persisted action resolution.

The evidence directory also contains the encrypted bundle's object inventory. A separate
least-privilege object-store job must verify every listed object exists with the same checksum;
missing objects block promotion. Application traffic stays disabled until schema health, tenant RLS,
OIDC, Temporal visibility, object reads, stream replay, and a signed action canary all pass.

## Migration rehearsal

Every destructive schema change is split across releases:

1. **expand** adds nullable/compatible schema;
2. **backfill** is resumable, bounded, and observed;
3. **read switch** lets old and new release probes coexist;
4. **contract** happens only after old binaries are drained and rollback has expired.

Use immutable probe executables extracted from the old and new release artifacts:

```bash
export LOOPGUARD_REHEARSAL_DATABASE_URL='postgresql://.../loopguard_rehearsal'
export LOOPGUARD_OLD_APP_PROBE=/release/old/control-api-probe
export LOOPGUARD_NEW_APP_PROBE=/release/new/control-api-probe
export LOOPGUARD_MIGRATION_EXPAND_REVISION=0002
export LOOPGUARD_MIGRATION_READ_SWITCH_REVISION=0003
export LOOPGUARD_MIGRATION_CONTRACT_REVISION=head
export LOOPGUARD_MIGRATION_MAX_TRANSITION_MS=30000
scripts/rehearse_migration.sh
```

Record dataset size, PostgreSQL version, migration duration, longest lock, blocked queries, replica
lag, both probe results, rollback point, and operator. Any lock over 30 seconds or observed old/new
incompatibility blocks release.

## Exercise cadence

- Automated integrity tests: every change.
- Isolated restore from a real encrypted backup: monthly.
- Migration rehearsal with production-scale sanitized data: every schema release.
- Regional failover including DNS and independent credentials: quarterly.

After an exercise, destroy the isolated environment through the approved infrastructure workflow,
retain only non-sensitive evidence, rotate temporary credentials, and file every missed target as a
release-blocking operational defect.
