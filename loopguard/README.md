# LoopGuard

**LoopGuard is a circuit breaker for AI agents. It catches runaway loops before they burn your API tokens.**

LoopGuard works in two layers:

- **Layer 1 — deterministic detection (always on, free, offline, no API key).** It normalizes agent events and flags repeated exact states, semantic near-duplicates, ping-pong exchanges, and budget runaway. Same input → same decision, every time.
- **Layer 2 — LLM judge (optional, any model).** When Layer 1 flags a suspected loop and a model is configured, LoopGuard asks a real LLM whether the agent is *genuinely* stuck or actually making progress, and — if stuck — generates a **specific correction** for that exact problem. This suppresses false positives and turns a flag into a real fix.

## Problem
Autonomous agents often silently loop: retrying the same tool with tiny parameter changes or passing the same failure between agents until your token bill grows.

## What it looks like
```text
[step 1] read_file("package.json") -> package.json not found
[step 2] read_file("./package.json") -> package.json not found
[step 3] read_file("../package.json") -> package.json not found

╭──────────────────╮
│ LoopGuard tripped │
╰──────────────────╯
Detector: semantic
Similarity: 0.91
Tool: read_file
Estimated tokens: 1284  cost: $0.004
                       Recent events
┏━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ agent      ┃ kind      ┃ tool      ┃ input/error           ┃
┡━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ repo-agent │ tool_call │ read_file │ package.json not found │
│ 2 │ repo-agent │ tool_call │ read_file │ package.json not found │
│ 3 │ repo-agent │ tool_call │ read_file │ package.json not found │
└───┴────────────┴───────────┴───────────┴───────────────────────┘
Judge: agent is stuck retrying the same path; this is a Python project (confidence 0.92)
Suggested fix: Stop looking for package.json — read pyproject.toml instead.
[t] terminate  [c] continue once  [a] allowlist  [i] inject correction
Action [t]:
```

## Installation
```bash
pip install -e ".[dev]"
```

## Quickstart
```python
from loopguard import LoopGuard, LoopEvent, LoopGuardConfig

guard = LoopGuard(LoopGuardConfig(action="warn"))
decision = guard.observe(LoopEvent(
    run_id="demo",
    agent="repo-agent",
    kind="tool_call",
    tool_name="read_file",
    tool_args={"path": "./package.json"},
    error="package.json not found",
))
```

## CLI demos
```bash
loopguard demo                                   # offline, no key, real failing tool, honest $0
loopguard demo --live                            # real LLM agent (Cerebras default), Layer 1 + 2
loopguard demo --live --model openai/gpt-4o      # any provider/model
loopguard demo --live --mode auto                # auto-repair: inject the fix and continue
loopguard demo --live --mode flag                # detect + report, non-blocking
loopguard demo --scenario pingpong               # A-B-A-B multi-agent loop
loopguard inspect runs/last.jsonl                # Rich table of the last run
loopguard init-config
```

The offline demo performs **real disk reads** against a real (empty) workspace, genuinely loops on missing files, and reports an honest **$0** (no LLM). The `--live` demo runs a **real** agent against real files: it genuinely loops hunting for `package.json` in a Python repo, the judge reasons that it should read `pyproject.toml` instead, and that real file is really read — real tokens, real cost, real recovery.

## Action modes
- `pause` (default) — interactive terminal: stop and let a human choose.
- `flag` — detect + report the loop and the suggested fix, non-blocking.
- `auto` — auto-repair: inject the LLM-generated correction and continue with no human.

## Providers (any model)
LoopGuard's judge and live agent are provider-agnostic via LiteLLM routing strings (`cerebras/gpt-oss-120b`, `openai/gpt-4o`, `anthropic/...`, `ollama/llama3`, ...). Pick one with `--model`/`--provider`.

```bash
pip install "loopguard[litellm]"   # universal provider (recommended)
pip install "loopguard[cerebras]"  # direct Cerebras SDK (minimal deps)
```

## Demo projects (real agents, no scripted loops)
The agents that loop are real LLM agents, not canned scripts. Each project is a real workspace
plus a system prompt that biases the agent toward the wrong well-known file, while the task is
solvable by reading a file that is actually present. The agent loops on its assumption; the
judge — which is given the workspace's real file listing — names the exact correct file; the
agent reads it and finishes. List and run them:

```bash
loopguard projects                              # npm-manifest, requirements-hunt, config-hunt, two-agent-manifest, custom
loopguard run npm-manifest --mode auto          # auto-repair: judge's fix injected, agent recovers
loopguard run config-hunt --mode pause          # interactive: [t]erminate [c]ontinue [a]llowlist [i]nject
loopguard run two-agent-manifest --mode auto    # two real agents ping-pong, guard catches it
loopguard run custom --task "Read settings.yaml and report the cache backend."  # any task you type
```

## Cloud app (mobile monitoring + intervention)
Run the engine as a server and monitor/intervene from a phone:
```bash
pip install "loopguard[server,cerebras]"   # or [server,litellm]
loopguard serve --port 8000                # FastAPI + WebSocket; loads CEREBRAS_API_KEY from .env
```
Then point the Expo app at it (see [`cloud-app/`](../cloud-app/README.md)). The app: picks a
project and mode, streams the live agent's tool calls + a real token/$ meter (agent **and**
judge cost), and on a detected loop (flag mode) shows the judge's reasoning + suggested fix with
**Approve fix / Continue once / Allow tool / Terminate** plus a free-text correction box. Auto
mode applies the fix and logs it. Separate tabs show running/past agents, the auto-fix feed, and
the allowlist. HTTP API the app uses: `GET /projects`, `POST /runs`, `GET /runs[/{id}]`,
`POST /runs/{id}/intervene`, `GET /allowlist`, `GET /autofixes`, `WS /runs/{id}/ws`.

> Scope: the server keeps run state **in memory** (restarting clears history) and ships with
> **CORS open and no auth** — fine for a local/LAN demo, not for a public deployment. Add an auth
> layer and a datastore before exposing it.

## How Layer 1 (deterministic) works
No API key. It normalizes events, redacts secrets, removes volatile fields, then embeds normalized text with a deterministic local HashingVectorizer from scikit-learn. Cosine similarity across the last `trip_count` states trips when all pairs exceed the threshold.

## Integrations
- **Manual:** create `LoopEvent`s and call `guard.observe(event, task=...)` before continuing. Use `wrap_tool()` for simple tool wrappers.
- **Any LiteLLM agent:** register one callback and every LLM call is guarded automatically — see [`examples/litellm_guarded_agent.py`](examples/litellm_guarded_agent.py):
  ```python
  import litellm
  from loopguard import LoopGuard, LoopGuardConfig
  from loopguard.integrations.litellm_callback import LoopGuardLiteLLMCallback

  guard = LoopGuard(LoopGuardConfig(action="flag"))
  litellm.callbacks = [LoopGuardLiteLLMCallback(guard)]
  ```

## Why not just LangSmith/Langfuse/AgentOps?
Observability platforms show traces after or during execution. LoopGuard intervenes locally during execution: it can pause, warn, raise, allowlist, or accept a correction prompt before more tokens are spent.

## Limitations
- Layer 1 hashing embeddings are fast and local, but match surface text — great for "same tool, tweaked args," weaker when an agent rephrases its reasoning each turn.
- Layer 2 (the LLM judge) costs real tokens and is not perfectly deterministic; it is opt-in and is only consulted after Layer 1 flags a candidate.
- The judge's fix is only as good as its repo context and the available tools. In the demos the
  judge is given the workspace file listing so it names the exact file; without that context it
  must guess. It can only suggest actions the agent's tools can actually perform.
- The core engine returns structured `LoopDecision`s with no terminal code, so the same engine
  drives the terminal UI, the FastAPI server, and the mobile app (see above).

## Hackathon demo script
See [`docs/demo_script.md`](docs/demo_script.md).
