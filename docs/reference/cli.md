# CLI reference

This file is generated from the installed Typer command tree. Do not edit help blocks by hand.
Regenerate it with `python -m loopguard.cli_docs` from the package directory.

## `loopguard`

```text

 Usage: loopguard [OPTIONS] COMMAND [ARGS]...

 LoopGuard semantic circuit breaker

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --install-completion          Install completion for the current shell.      │
│ --show-completion             Show completion for the current shell, to copy │
│                               it or customize the installation.              │
│ --help                        Show this message and exit.                    │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ quickstart    Prove offline loop protection through the real daemon in under │
│               two minutes.                                                   │
│ doctor        Check daemon, storage, encryption, dispatch, and integration   │
│               health.                                                        │
│ setup         Prepare a local protected-session integration without          │
│               approving vendor trust.                                        │
│ uninstall     Remove only LoopGuard-owned integrations and retain all local  │
│               event data.                                                    │
│ feedback      Print a version-prefilled public support route without sending │
│               data.                                                          │
│ sessions      List locally observed sessions and exact attached-hook         │
│               coverage.                                                      │
│ explain       Print the cause, safe fixes, and local reference for an error  │
│               code.                                                          │
│ demo                                                                         │
│ projects      List the demo projects an agent can really run.                │
│ run           Run a real agent on a demo project and guard it live in the    │
│               terminal.                                                      │
│ inspect                                                                      │
│ init-config                                                                  │
│ serve                                                                        │
│ daemon        Manage the owner-only local LoopGuard daemon.                  │
│ config        Inspect and validate layered LoopGuard configuration.          │
│ integrations  Install and verify native agent integrations.                  │
│ data          Manage owner-only local LoopGuard data.                        │
│ dx            Inspect privacy-safe local developer-experience metrics.       │
│ router        Validate and evaluate deterministic model routing.             │
│ update        Verify signed LoopGuard release manifests.                     │
│ migrate       Check and apply backup-first local schema migrations.          │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard quickstart`

```text

 Usage: loopguard quickstart [OPTIONS]

 Prove offline loop protection through the real daemon in under two minutes.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --home        PATH  Parent for isolated temporary state.                     │
│ --json              Emit stable machine-readable output.                     │
│ --help              Show this message and exit.                              │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard setup`

```text

 Usage: loopguard setup [OPTIONS]

 Prepare a local protected-session integration without approving vendor trust.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --agent                  TEXT  auto, codex, claude, or comma-separated.      │
│                                [default: auto]                               │
│ --scope                  TEXT  user or project.                              │
│ --dry-run                      Preview every change without writes.          │
│ --non-interactive              Require explicit agents and scope with stable │
│                                errors.                                       │
│ --resume-from            TEXT  Resume at a named step.                       │
│ --home                   PATH  Override LOOPGUARD_HOME.                      │
│ --json                         Emit stable machine-readable output.          │
│ --help                         Show this message and exit.                   │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard uninstall`

```text

 Usage: loopguard uninstall [OPTIONS]

 Remove only LoopGuard-owned integrations and retain all local event data.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --agent          TEXT  auto, codex, claude, or comma-separated.              │
│                        [default: auto]                                       │
│ --scope          TEXT  user or project. [default: user]                      │
│ --dry-run              Preview owned removals only.                          │
│ --home           PATH  Override LOOPGUARD_HOME.                              │
│ --json                 Emit stable machine-readable output.                  │
│ --help                 Show this message and exit.                           │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard sessions`

```text

 Usage: loopguard sessions [OPTIONS]

 List locally observed sessions and exact attached-hook coverage.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --home        PATH  Override LOOPGUARD_HOME.                                 │
│ --json              Emit stable machine-readable output.                     │
│ --help              Show this message and exit.                              │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard feedback`

```text

 Usage: loopguard feedback [OPTIONS]

 Print a version-prefilled public support route without sending data.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard daemon`

```text

 Usage: loopguard daemon [OPTIONS] COMMAND [ARGS]...

 Manage the owner-only local LoopGuard daemon.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ start      Start the local daemon without unmanaged background forks.        │
│ install    Install or upgrade the current user's automatic LoopGuard daemon  │
│            service.                                                          │
│ uninstall  Stop and remove only the current user's LoopGuard-owned service   │
│            definition.                                                       │
│ status     Report user-service installation plus live daemon reachability.   │
│ doctor     Run the same full diagnostics as `loopguard doctor`.              │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard daemon start`

```text

 Usage: loopguard daemon start [OPTIONS]

 Start the local daemon without unmanaged background forks.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --foreground              Run attached for launchd, systemd, containers, and │
│                           debugging.                                         │
│ --home              PATH  Override LOOPGUARD_HOME.                           │
│ --json                    Emit stable machine-readable output.               │
│ --help                    Show this message and exit.                        │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard daemon install`

```text

 Usage: loopguard daemon install [OPTIONS]

 Install or upgrade the current user's automatic LoopGuard daemon service.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --executable        PATH  Absolute LoopGuard console-script path; detected   │
│                           by default.                                        │
│ --home              PATH  Override LOOPGUARD_HOME.                           │
│ --dry-run                 Preview without writing a service.                 │
│ --json                    Emit stable machine-readable output.               │
│ --help                    Show this message and exit.                        │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard daemon status`

```text

 Usage: loopguard daemon status [OPTIONS]

 Report user-service installation plus live daemon reachability.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json                                                                       │
│ --home        PATH                                                           │
│ --help              Show this message and exit.                              │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard daemon doctor`

```text

 Usage: loopguard daemon doctor [OPTIONS]

 Run the same full diagnostics as `loopguard doctor`.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json                                                                       │
│ --verbose                                                                    │
│ --home           PATH                                                        │
│ --help                 Show this message and exit.                           │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard daemon uninstall`

```text

 Usage: loopguard daemon uninstall [OPTIONS]

 Stop and remove only the current user's LoopGuard-owned service definition.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --home           PATH  Override LOOPGUARD_HOME.                              │
│ --dry-run              Preview owned service removal.                        │
│ --json                 Emit stable machine-readable output.                  │
│ --help                 Show this message and exit.                           │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard doctor`

```text

 Usage: loopguard doctor [OPTIONS]

 Check daemon, storage, encryption, dispatch, and integration health.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json                        Emit stable machine-readable output.           │
│ --verbose                     Add redacted local diagnostics.                │
│ --home                  PATH  Override LOOPGUARD_HOME.                       │
│ --fix-safe                    Repair LoopGuard-owned state only.             │
│ --bundle                PATH  Write a redacted diagnostic ZIP.               │
│ --confirm-bundle              Confirm the displayed diagnostic redaction     │
│                               preview.                                       │
│ --help                        Show this message and exit.                    │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard explain`

```text

 Usage: loopguard explain [OPTIONS] CODE

 Print the cause, safe fixes, and local reference for an error code.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    code      TEXT  Stable LoopGuard error code. [required]                 │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json          Emit stable machine-readable output.                         │
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard config`

```text

 Usage: loopguard config [OPTIONS] COMMAND [ARGS]...

 Inspect and validate layered LoopGuard configuration.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ path      Print the active configuration file path.                          │
│ show      Show effective redacted values, sources, and precedence.           │
│ validate  Validate a configuration file without starting the daemon.         │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard config path`

```text

 Usage: loopguard config path [OPTIONS]

 Print the active configuration file path.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json                                                                       │
│ --home        PATH                                                           │
│ --help              Show this message and exit.                              │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard config show`

```text

 Usage: loopguard config show [OPTIONS]

 Show effective redacted values, sources, and precedence.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json                                                                       │
│ --home        PATH                                                           │
│ --help              Show this message and exit.                              │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard config validate`

```text

 Usage: loopguard config validate [OPTIONS] [PATH]

 Validate a configuration file without starting the daemon.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   [path]      PATH                                                           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json                                                                       │
│ --home        PATH                                                           │
│ --help              Show this message and exit.                              │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations`

```text

 Usage: loopguard integrations [OPTIONS] COMMAND [ARGS]...

 Install and verify native agent integrations.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ install    Install one native agent integration.                             │
│ verify     Verify one native agent integration.                              │
│ uninstall  Uninstall one native agent integration.                           │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations install`

```text

 Usage: loopguard integrations install [OPTIONS] COMMAND [ARGS]...

 Install one native agent integration.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ codex   Install LoopGuard's checksum-pinned Codex integration.               │
│ claude  Install LoopGuard's Claude Code plugin or reviewed fallback hooks.   │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations install codex`

```text

 Usage: loopguard integrations install codex [OPTIONS]

 Install LoopGuard's checksum-pinned Codex integration.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --fallback                      Use hooks.json only when this Codex version  │
│                                 cannot activate plugins.                     │
│ --scope                   TEXT  Fallback scope: user or project.             │
│                                 [default: user]                              │
│ --settings                PATH  Fallback hooks.json path.                    │
│ --repository              PATH  Repository for project fallback scope.       │
│ --executable              TEXT  LoopGuard executable for fallback hooks.     │
│                                 [default: loopguard]                         │
│ --codex-executable        TEXT  Codex executable for plugin install.         │
│                                 [default: codex]                             │
│ --codex-version           TEXT  Explicit compatibility version probe.        │
│ --home                    PATH  Override LOOPGUARD_HOME for plugin staging.  │
│ --dry-run                       Preview without changing Codex.              │
│ --json                          Emit stable machine-readable output.         │
│ --help                          Show this message and exit.                  │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations install claude`

```text

 Usage: loopguard integrations install claude [OPTIONS]

 Install LoopGuard's Claude Code plugin or reviewed fallback hooks.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --fallback                       Use settings hooks only when this Claude    │
│                                  version cannot activate plugins.            │
│ --cloud                          Prepare project hooks for signed HTTPS      │
│                                  fallback in Claude Code remote sessions.    │
│ --scope                    TEXT  Plugin scope, or fallback scope: user or    │
│                                  project.                                    │
│                                  [default: user]                             │
│ --settings                 PATH  Fallback settings.json path.                │
│ --repository               PATH  Repository for project fallback scope.      │
│ --executable               TEXT  LoopGuard executable for fallback hooks.    │
│                                  [default: loopguard]                        │
│ --claude-executable        TEXT  Claude Code executable for plugin install.  │
│                                  [default: claude]                           │
│ --claude-version           TEXT  Explicit compatibility version probe.       │
│ --home                     PATH  Override LOOPGUARD_HOME for plugin staging. │
│ --dry-run                        Preview without changing Claude Code.       │
│ --json                           Emit stable machine-readable output.        │
│ --help                           Show this message and exit.                 │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations verify`

```text

 Usage: loopguard integrations verify [OPTIONS] COMMAND [ARGS]...

 Verify one native agent integration.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ codex   Verify exact installation, discovery, and Codex-reported hook trust. │
│ claude  Verify exact Claude installation bytes and managed-hook policy.      │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations verify codex`

```text

 Usage: loopguard integrations verify codex [OPTIONS]

 Verify exact installation, discovery, and Codex-reported hook trust.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --settings                PATH  Fallback hooks.json path.                    │
│ --scope                   TEXT  Fallback scope: user or project.             │
│                                 [default: user]                              │
│ --executable              TEXT  Executable recorded in fallback hooks.       │
│                                 [default: loopguard]                         │
│ --codex-executable        TEXT  Codex executable for plugin verification.    │
│                                 [default: codex]                             │
│ --cwd                     PATH  Working directory for effective Codex hook   │
│                                 lookup (defaults to current directory).      │
│ --json                          Emit stable machine-readable output.         │
│ --help                          Show this message and exit.                  │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations verify claude`

```text

 Usage: loopguard integrations verify claude [OPTIONS]

 Verify exact Claude installation bytes and managed-hook policy.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --settings                 PATH  Fallback settings.json path; omit for       │
│                                  plugin.                                     │
│ --scope                    TEXT  Fallback scope: user or project.            │
│                                  [default: user]                             │
│ --repository               PATH  Repository for project fallback scope.      │
│ --executable               TEXT  Executable recorded in fallback hooks.      │
│                                  [default: loopguard]                        │
│ --claude-executable        TEXT  Claude Code executable for plugin           │
│                                  verification.                               │
│                                  [default: claude]                           │
│ --json                           Emit stable machine-readable output.        │
│ --help                           Show this message and exit.                 │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations uninstall`

```text

 Usage: loopguard integrations uninstall [OPTIONS] COMMAND [ARGS]...

 Uninstall one native agent integration.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ codex   Remove only the exact LoopGuard Codex plugin or fallback handlers.   │
│ claude  Remove only exact LoopGuard Claude plugin or fallback handlers.      │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations uninstall codex`

```text

 Usage: loopguard integrations uninstall codex [OPTIONS]

 Remove only the exact LoopGuard Codex plugin or fallback handlers.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --settings                PATH  Fallback hooks.json path; omit for plugin.   │
│ --scope                   TEXT  Fallback scope: user or project.             │
│                                 [default: user]                              │
│ --repository              PATH  Repository for project fallback scope.       │
│ --executable              TEXT  Executable recorded in fallback hooks.       │
│                                 [default: loopguard]                         │
│ --codex-executable        TEXT  Codex executable for plugin removal.         │
│                                 [default: codex]                             │
│ --json                          Emit stable machine-readable output.         │
│ --help                          Show this message and exit.                  │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard integrations uninstall claude`

```text

 Usage: loopguard integrations uninstall claude [OPTIONS]

 Remove only exact LoopGuard Claude plugin or fallback handlers.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --settings                 PATH  Fallback settings.json path; omit for       │
│                                  plugin.                                     │
│ --scope                    TEXT  Fallback scope: user or project.            │
│                                  [default: user]                             │
│ --repository               PATH  Repository for project fallback scope.      │
│ --executable               TEXT  Executable recorded in fallback hooks.      │
│                                  [default: loopguard]                        │
│ --claude-executable        TEXT  Claude Code executable for plugin removal.  │
│                                  [default: claude]                           │
│ --json                           Emit stable machine-readable output.        │
│ --help                           Show this message and exit.                 │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard data`

```text

 Usage: loopguard data [OPTIONS] COMMAND [ARGS]...

 Manage owner-only local LoopGuard data.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ purge  Permanently remove owner-controlled local data after explicit         │
│        confirmation.                                                         │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard data purge`

```text

 Usage: loopguard data purge [OPTIONS]

 Permanently remove owner-controlled local data after explicit confirmation.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --confirm              Permanently delete local LoopGuard data.              │
│ --home           PATH  Override LOOPGUARD_HOME.                              │
│ --json                 Emit stable machine-readable output.                  │
│ --help                 Show this message and exit.                           │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard dx`

```text

 Usage: loopguard dx [OPTIONS] COMMAND [ARGS]...

 Inspect privacy-safe local developer-experience metrics.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ report  Report setup timing and outcomes; upload is always off by default.   │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard dx report`

```text

 Usage: loopguard dx report [OPTIONS]

 Report setup timing and outcomes; upload is always off by default.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --local              Read local-only, privacy-safe timings.                  │
│ --home         PATH  Override LOOPGUARD_HOME.                                │
│ --json               Emit stable machine-readable output.                    │
│ --help               Show this message and exit.                             │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard router`

```text

 Usage: loopguard router [OPTIONS] COMMAND [ARGS]...

 Validate and evaluate deterministic model routing.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ evaluate  Compare observed holdout outcomes; never report estimated savings  │
│           as fact.                                                           │
│ policy    Validate versioned routing policies.                               │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard router evaluate`

```text

 Usage: loopguard router evaluate [OPTIONS]

 Compare observed holdout outcomes; never report estimated savings as fact.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --since                      TEXT                    Window such as 30d or   │
│                                                      24h.                    │
│                                                      [default: 30d]          │
│ --json                                               Emit stable             │
│                                                      machine-readable        │
│                                                      output.                 │
│ --home                       PATH                    Override                │
│                                                      LOOPGUARD_HOME.         │
│ --minimum-sample-size        INTEGER RANGE           [default: 30]           │
│                              [1<=x<=1000000]                                 │
│ --help                                               Show this message and   │
│                                                      exit.                   │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard router policy`

```text

 Usage: loopguard router policy [OPTIONS] COMMAND [ARGS]...

 Validate versioned routing policies.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ validate  Validate a strict, versioned routing policy without invoking a     │
│           model.                                                             │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard router policy validate`

```text

 Usage: loopguard router policy validate [OPTIONS] PATH

 Validate a strict, versioned routing policy without invoking a model.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    path      PATH  [required]                                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json          Emit stable machine-readable output.                         │
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard update`

```text

 Usage: loopguard update [OPTIONS] COMMAND [ARGS]...

 Verify signed LoopGuard release manifests.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ check  Verify release metadata without downloading or executing an artifact. │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard update check`

```text

 Usage: loopguard update check [OPTIONS]

 Verify release metadata without downloading or executing an artifact.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ *  --manifest          FILE  [required]                                      │
│ *  --public-key        FILE  [required]                                      │
│    --json                    Emit stable machine-readable output.            │
│    --help                    Show this message and exit.                     │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard migrate`

```text

 Usage: loopguard migrate [OPTIONS] COMMAND [ARGS]...

 Check and apply backup-first local schema migrations.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ check    Inspect migration compatibility without modifying local state.      │
│ apply    Create and verify an encrypted backup, then apply forward-only      │
│          migrations.                                                         │
│ restore  Restore a verified encrypted backup after an exact operator         │
│          confirmation.                                                       │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard migrate check`

```text

 Usage: loopguard migrate check [OPTIONS]

 Inspect migration compatibility without modifying local state.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --database        PATH  Override the local events database.                  │
│ --home            PATH  Override LOOPGUARD_HOME.                             │
│ --json                  Emit stable machine-readable output.                 │
│ --help                  Show this message and exit.                          │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard migrate apply`

```text

 Usage: loopguard migrate apply [OPTIONS]

 Create and verify an encrypted backup, then apply forward-only migrations.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --database        PATH  Override the local events database.                  │
│ --home            PATH  Override LOOPGUARD_HOME.                             │
│ --yes                   Apply the previewed forward migration.               │
│ --json                  Emit stable machine-readable output.                 │
│ --help                  Show this message and exit.                          │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard migrate restore`

```text

 Usage: loopguard migrate restore [OPTIONS]

 Restore a verified encrypted backup after an exact operator confirmation.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ *  --backup          FILE  [required]                                        │
│ *  --database        FILE  [required]                                        │
│    --yes                   Replace the database from this backup.            │
│    --help                  Show this message and exit.                       │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard init-config`

```text

 Usage: loopguard init-config [OPTIONS]

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --path        PATH  [default: loopguard.json]                                │
│ --help              Show this message and exit.                              │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard demo`

```text

 Usage: loopguard demo [OPTIONS]

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --live                                              Run a real LLM agent     │
│                                                     (needs an API key).      │
│ --model                     TEXT                    Model id (litellm        │
│                                                     routing string).         │
│                                                     [default:                │
│                                                     cerebras/gpt-oss-120b]   │
│ --provider                  TEXT                    auto | litellm |         │
│                                                     cerebras                 │
│                                                     [default: auto]          │
│ --mode                      [pause|flag|auto|warn]  pause | flag | auto |    │
│                                                     warn                     │
│                                                     [default: pause]         │
│ --scenario                  [single|pingpong|cereb  [default: single]        │
│                             ras]                                             │
│ --guard       --no-guard                            [default: guard]         │
│ --help                                              Show this message and    │
│                                                     exit.                    │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard projects`

```text

 Usage: loopguard projects [OPTIONS]

 List the demo projects an agent can really run.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard run`

```text

 Usage: loopguard run [OPTIONS] PROJECT_ID

 Run a real agent on a demo project and guard it live in the terminal.

 In `pause` mode you get the full terminate / continue / allowlist / inject
 flow;
 `auto` applies the judge's fix; `flag` reports without blocking.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    project_id      TEXT  Project id (see `loopguard projects`). [required] │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --mode            [pause|flag|auto|warn]  pause | flag | auto | warn         │
│                                           [default: pause]                   │
│ --model           TEXT                    Model id.                          │
│                                           [default: cerebras/gpt-oss-120b]   │
│ --provider        TEXT                    auto | litellm | cerebras          │
│                                           [default: auto]                    │
│ --task            TEXT                    Override the project task (custom  │
│                                           project).                          │
│ --help                                    Show this message and exit.        │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard inspect`

```text

 Usage: loopguard inspect [OPTIONS] PATH

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    path      PATH  [required]                                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯

```

## `loopguard serve`

```text

 Usage: loopguard serve [OPTIONS]

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --host        TEXT     [default: 127.0.0.1]                                  │
│ --port        INTEGER  [default: 8000]                                       │
│ --help                 Show this message and exit.                           │
╰──────────────────────────────────────────────────────────────────────────────╯

```
