# Versioning and compatibility

LoopGuard versions product packages with Semantic Versioning and versions durable contracts
independently. A package release does not silently reinterpret stored data or remove an API field.

## Version domains

| Domain | Current | Compatibility rule |
|---|---:|---|
| Python package/CLI | `0.1.x` development line | SemVer; behavior changes appear in the changelog |
| Control API | OpenAPI `info.version` + exported schema | Removing an operation, required response field, or enum value requires a major contract version |
| Local event schema | `ControlEvent.schema_version` | Readers accept documented older versions; writers emit one current version |
| Action canonicalization | `canonicalization_version: 1` | Any byte-level change requires a new version; old challenges retain old bytes until expiry |
| Local SQLite schema | `PRAGMA user_version: 3` | Forward-only, backup-first migrations; newer schemas fail closed |
| Control database | Alembic revision | Expand/backfill/read-switch/contract; mixed-version window documented per release |
| Repair workflow | Workflow/search schema version | Deterministic replay compatibility; breaking changes use a new workflow type/version |
| Web/iOS client contract | Generated OpenAPI snapshot | CI rejects generated-client drift and incompatible API removal |

## Supported compatibility matrix

The unreleased production line supports local SQLite schemas 1–3 migrating to 3, action
canonicalization 1, and the checked-in control OpenAPI contract. Hosted API, web, iOS, worker, and
database revisions must come from the same signed release manifest. Local guarding continues when
the hosted service is newer, unavailable, or quota-limited.

`loopguard migrate check --json` is read-only and reports the current/target schema and exact
steps. `loopguard migrate apply --yes` creates and verifies an encrypted owner-only backup before
forward migration. A newer or damaged schema is refused rather than downgraded or reinterpreted.

`loopguard update check --manifest FILE --public-key FILE --json` verifies the release channel,
version, minimum database schema, HTTPS artifact URL, SHA-256 digest, and Ed25519 signature. It
does not download or execute the artifact.

## Deprecation contract

Every deprecation names:

- the old command/API/surface;
- its replacement;
- the earliest removal version and date when known;
- a migration document;
- a runtime warning that remains machine-readable.

The Expo `cloud-app` prototype is deprecated in the unreleased production line. Replacement:
`apps/web` and `apps/ios`. Earliest removal: `0.3.0`, after at least one fallback release and
migration review. New security/workflow behavior is not backported to the prototype.

## Release checklist

Any breaking or behavior-changing release updates this document, `CHANGELOG.md`, generated
contracts, compatibility tests, and the relevant migration guide. Release artifacts are not
publishable until their manifest contains checksum, SBOM, provenance, and signature evidence.
