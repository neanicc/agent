# Offline quickstart

LoopGuard can prove its core protection path without an API key, cloud account, Docker, or permanent hook installation.

## 1. Install

From this repository:

```bash
cd loopguard
python -m venv .venv
source .venv/bin/activate
pip install -e ".[control]"
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead. The Windows daemon transport remains capability-disabled until its named-pipe backend passes native CI; the engine itself is still testable on Windows.

## 2. Run the real protected path

```bash
loopguard quickstart
```

Expected output:

```text
Loop detected before another paid turn.
Protected path: 3 encrypted events → real LoopGuard.
No model or API key used. No telemetry sent. Temporary state removed.
Next: loopguard setup --agent auto
```

The command starts the real daemon in-process, sends three hook-shaped tool events through the framed local protocol, redacts and encrypts them in temporary SQLite state, projects them into the existing detector, receives a `request_approval` policy decision, and removes all temporary state. It never reads provider-key environment variables.

For automation, use stable JSON:

```bash
loopguard quickstart --json
```

## 3. Protect a real agent

`loopguard setup --agent auto` is delivered by the managed integration milestone. Until then, run the daemon attached for local development:

```bash
loopguard daemon start --foreground
```

Background startup deliberately refuses to fork itself. Managed launchd/systemd/Windows service installation belongs to the integration workflow so lifecycle and uninstall remain auditable.

## Troubleshooting

```bash
loopguard doctor
loopguard doctor --json
loopguard explain LGD-DAEMON-001
loopguard config show --json
```

The offline magical moment should take seconds after installation. The production target for a protected Codex or Claude session is under five minutes once managed adapters ship.
