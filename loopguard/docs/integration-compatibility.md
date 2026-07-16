# Agent integration compatibility

LoopGuard reports capabilities from runtime evidence. A configured file is not proof that an
agent loaded or trusted it, and a repository-local hook is not proof that a hosted agent executes
it. Use `loopguard doctor --json` for the authoritative report on the current machine.

Capability states mean:

- `supported`: the required local components and runtime evidence were verified.
- `conditional`: the integration exists, but a documented prerequisite or live smoke remains.
- `experimental`: implemented but not yet validated on the required production platform matrix.
- `unavailable`: the surface does not expose the control, or LoopGuard does not implement it.

## Capability matrix

The table below assumes both vendor CLIs are installed but no attached integration is configured.
It is rendered by the same capability model used by `loopguard doctor`. Setup changes applicable
attached cells to `conditional`; verified hooks plus a reachable daemon change exact covered cells
to `supported`.

| Surface | Observe | Block tool | Request approval | Start managed | Inject | Interrupt | Select model | Select effort |
|---|---|---|---|---|---|---|---|---|
| Codex, attached local | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |
| Codex, managed local | conditional | conditional | conditional | conditional | conditional | conditional | conditional | conditional |
| Codex Cloud | conditional | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |
| Claude Code, attached local | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |
| Claude Code, managed local | conditional | conditional | conditional | conditional | conditional | conditional | conditional | conditional |
| Claude Code on the web | conditional | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |

Cloud observation remains `conditional` until a real signed-delivery compatibility smoke records
an event. LoopGuard never infers cloud model selection, effort selection, interruption, injection,
or tool blocking from local hook configuration.

## Evidence in the doctor report

Each surface reports all capability names plus:

- installed vendor version;
- active integration source (`plugin`, `fallback`, `managed_bridge`, `not_configured`, or
  `probe_failed`);
- pinned plugin version and checksum where available;
- hook locations (local output only);
- observable trust status;
- exact event coverage;
- daemon reachability and managed-bridge health.

Example:

```bash
loopguard doctor --json
```

The existing daemon, store, protocol, and error fields remain at the top level.
`agent_integrations.surfaces` contains the matrix. Automation should branch on explicit capability
states, never on the presence of a settings file alone.

## Safe maintenance boundaries

```bash
loopguard doctor --fix-safe --json
loopguard uninstall --dry-run
loopguard data purge --confirm
```

`doctor --fix-safe` can repair owner-only permissions for real LoopGuard files. The service task
can additionally restart LoopGuard's own service and regenerate its owned wrappers. It never
changes vendor trust, credentials, policy, or unrelated configuration.

`uninstall` removes only exact LoopGuard plugin/fallback definitions and retains the encrypted
event store. Data purge is deliberately separate and requires `--confirm`. Neither command treats
similar-looking third-party hooks as LoopGuard-owned.

To share diagnostics, first preview the fixed contents and redactions:

```bash
loopguard doctor --bundle ./loopguard-diagnostics.zip
# Review the preview, then:
loopguard doctor --bundle ./loopguard-diagnostics.zip --confirm-bundle
```

The archive excludes credentials, prompts, repository names, session identifiers, and absolute
hook paths. No upload occurs.
