# Changelog

All notable changes are recorded here. LoopGuard packages follow Semantic Versioning; API,
protocol, event, workflow, and database contracts have explicit independent versions.

## [Unreleased]

### Added

- Durable local event capture, repository/agent integrations, context coordination, deterministic
  verification, routing/cost controls, and bounded Auto-Heal repair workflows.
- Multi-tenant control API, capability discovery, artifact protection, signed actions, web
  operations console, and native iOS safety client.
- Production threat model, tenant/object action authorization, hook path containment, owner-only
  state validation, signed update-manifest verification, and backup-first local migrations.

### Security

- Session actions require the target session and host to belong to the authenticated tenant.
- Signed-action acceptance requires the authenticated tenant and user who requested the action.
- Untrusted hook artifact paths cannot traverse outside local state or follow symlinks.
- Downloaded updates remain inert until both the signed manifest and artifact digest verify.

### Deprecated

- `cloud-app` Expo prototype. Use `apps/web` and `apps/ios`. It remains available for one release
  and is scheduled for removal no earlier than `0.3.0`; see
  `docs/product/control-surfaces.md`.

## [0.1.0] - 2026-06-27

- Initial local LoopGuard prototype and demonstration server.
