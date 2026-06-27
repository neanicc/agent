# LoopGuard

**LoopGuard is a local semantic circuit breaker for AI agents. It catches runaway loops before they burn your API tokens.**

LoopGuard wraps agent LLM/tool-call events, detects repeated exact states, semantic near-duplicates, ping-pong exchanges, and budget runaway, then warns or pauses locally in your terminal.

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
loopguard demo --mode no-guard
loopguard demo
loopguard demo --scenario pingpong
loopguard init-config
loopguard inspect runs/last.jsonl
```

## How semantic detection works
Core LoopGuard needs no API key. It normalizes events, redacts secrets, removes volatile fields, then embeds normalized text with a deterministic local HashingVectorizer-style embedder from scikit-learn. Cosine similarity across the last `trip_count` states trips when all pairs exceed the threshold.

## Manual integration
Create `LoopEvent` objects for LLM calls, tool calls, tool results, agent messages, or errors and call `guard.observe(event)` before continuing execution. Use `wrap_tool()` for simple tool wrappers.

## Cerebras setup
Core LoopGuard does not need Cerebras. Cerebras is only for a live LLM/tool-calling demo; local semantic detection remains provider-independent.

```bash
export CEREBRAS_API_KEY="..."
export CEREBRAS_MODEL="gpt-oss-120b"
pip install "loopguard[cerebras]"
loopguard demo --scenario cerebras
```

## Why not just LangSmith/Langfuse/AgentOps?
Observability platforms show traces after or during execution. LoopGuard intervenes locally during execution: it can pause, warn, raise, allowlist, or accept a correction prompt before more tokens are spent.

## Limitations
- Hashing embeddings are fast and local, not as nuanced as large embedding models.
- The terminal pause UI is designed for development, not unattended production.
- Integrations are intentionally lightweight for the MVP.

## Hackathon demo script
See [`docs/demo_script.md`](docs/demo_script.md).
