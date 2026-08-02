# Tutorial: prove your first protected session

This tutorial demonstrates LoopGuard’s real local decision path without changing an agent’s global
configuration.

## Before you start

You need Python 3.11 or newer and a POSIX shell. No API key, model, Docker daemon, or cloud service
is used.

## Run the proof

```bash
git clone https://github.com/neanicc/agent.git
cd agent/loopguard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[control]"
loopguard quickstart
```

Look for:

```text
Loop detected before another paid turn.
Protected path: 3 encrypted events → real LoopGuard.
```

## What just happened

The command created temporary owner-only state, started the production local protocol in-process,
sent three synthetic repeated-tool events, validated and redacted them, committed encrypted event
records, restored detector state, and returned a policy decision requesting approval. It then
removed the temporary state.

That distinction matters: this is not a screenshot or hard-coded success message. It exercises the
same synchronous safety path an adapter calls before the next agent turn.

## Inspect machine-readable output

```bash
loopguard quickstart --json
```

Use this form in smoke tests. Fields and error codes are versioned; prose is for humans.

## Diagnose your environment

```bash
loopguard doctor
loopguard doctor --json
loopguard config show --json
```

The doctor reports unsupported or unsafe capabilities instead of enabling them optimistically.
Continue with [the product guide](../getting-started/product-guide.md) when the local proof passes.
