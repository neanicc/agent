# Protect an existing Codex or Claude Code session

This local path normally takes under five minutes. It keeps vendor trust under your control and
does not enable telemetry or upload prompts.

## 1. Preview, then set up

For user-wide protection of detected local agents:

```bash
loopguard setup --agent auto --scope user --dry-run
loopguard setup --agent auto --scope user
```

For one repository, run this inside that repository:

```bash
loopguard setup --agent auto --scope project --dry-run
loopguard setup --agent auto --scope project
```

Setup checks installed versions and daemon dependencies, installs the user service, selects one
compatible integration source per agent (plugin when supported within the requested scope,
otherwise one fallback), starts the daemon, and sends a synthetic event. Project scope uses
repository-owned fallback hooks when global plugin activation cannot honor that boundary. Existing
settings and unrelated hooks are preserved.
Repeated setup is a no-op.

Expected final status before trust is deliberately honest:

```text
Status: attention_required
codex trust review: Open Codex /hooks; verify LoopGuard's exact definition and choose trust yourself.
```

LoopGuard never clicks, writes, or bypasses a vendor trust decision.

## 2. Complete the explicit trust review

For Codex, open `/hooks`, find the LoopGuard source and checksum shown by setup, review the exact
commands, and choose trust in Codex yourself. Then run:

```bash
loopguard integrations verify codex --json
```

For Claude Code, review the exact local plugin or fallback hook source reported by setup, then run:

```bash
loopguard integrations verify claude --json
```

Continue only when verification reports the expected source and coverage. A `trust_required`,
`hooks_not_discovered`, `incomplete`, or `conditional` result is not full protection.

## 3. Start the agent normally

No wrapper shell is required:

```bash
codex
# or
claude
```

After the first lifecycle event, check local status:

```bash
loopguard sessions
```

Expected shape:

```text
codex protected_attached · session … · coverage: SessionStart, PreToolUse, PostToolUse, …
```

`protected_attached` means LoopGuard observed the session through attached hooks. The coverage list
is exact for the verified vendor/version; it is not a generic “fully protected” claim. Controls
outside that list remain unavailable or conditional. Use `loopguard doctor --json` to inspect every
capability state.

## Attention states

| State | Meaning | Next action |
|---|---|---|
| `trust_required` | Files are installed, but Codex has not reported explicit trust. | Review `/hooks`, decide trust yourself, then verify again. |
| `hooks_not_discovered` | Installed bytes were not present in the agent's effective hook list. | Confirm scope and current repository; run `loopguard doctor --json`. |
| `incomplete` | Only part of the expected hook set is active. | Preview setup again; inspect conflicts without deleting unrelated hooks. |
| `probe_failed` | A bounded local CLI probe failed. | Run the vendor `--version` command, then retry doctor with `--json`. |
| daemon `unreachable` | Hooks cannot reach the owner-only local daemon. | Run `loopguard daemon status --json`; reinstall/restart only LoopGuard's service. |
| cloud `conditional` | A repo hook exists but no real hosted delivery smoke is recorded. | Do not assume cloud protection; complete the documented signed smoke first. |
| setup `failed` | One resumable step failed. | Run the exact `resume_command` printed in JSON or human output. |

## Local diagnostics and setup timing

```bash
loopguard doctor --fix-safe --json
loopguard dx report --local --json
loopguard feedback
```

The DX report stores only fixed step names, durations, timestamps, and outcome codes. It excludes
paths, repository names, prompts, credentials, and identifiers. Upload is off by default. The
feedback command prints a version-prefilled public issue route and sends nothing automatically.

To remove the integration while keeping local history:

```bash
loopguard uninstall --dry-run
loopguard uninstall
```

Permanent local data deletion is separate: `loopguard data purge --confirm`.
