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

## `loopguard daemon`

```text

 Usage: loopguard daemon [OPTIONS] COMMAND [ARGS]...

 Manage the owner-only local LoopGuard daemon.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ start   Start the local daemon without unmanaged background forks.           │
│ status  Report whether the configured daemon endpoint is reachable.          │
│ doctor  Run the same full diagnostics as `loopguard doctor`.                 │
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

## `loopguard daemon status`

```text

 Usage: loopguard daemon status [OPTIONS]

 Report whether the configured daemon endpoint is reachable.

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

## `loopguard doctor`

```text

 Usage: loopguard doctor [OPTIONS]

 Check daemon, storage, encryption, dispatch, and integration health.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --json                 Emit stable machine-readable output.                  │
│ --verbose              Add redacted local diagnostics.                       │
│ --home           PATH  Override LOOPGUARD_HOME.                              │
│ --help                 Show this message and exit.                           │
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
│ codex  Install LoopGuard's checksum-pinned Codex integration.                │
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

## `loopguard integrations verify`

```text

 Usage: loopguard integrations verify [OPTIONS] COMMAND [ARGS]...

 Verify one native agent integration.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ codex  Verify exact installation, discovery, and Codex-reported hook trust.  │
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

## `loopguard integrations uninstall`

```text

 Usage: loopguard integrations uninstall [OPTIONS] COMMAND [ARGS]...

 Uninstall one native agent integration.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ codex  Remove only the exact LoopGuard Codex plugin or fallback handlers.    │
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
│ --host        TEXT     [default: 0.0.0.0]                                    │
│ --port        INTEGER  [default: 8000]                                       │
│ --help                 Show this message and exit.                           │
╰──────────────────────────────────────────────────────────────────────────────╯

```
