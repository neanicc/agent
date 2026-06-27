# LoopGuard

**A circuit breaker for AI agents.** It catches runaway loops before they burn your API
tokens — deterministically first (free, offline), then with an LLM judge that decides whether
the agent is *genuinely* stuck and, if so, writes a specific correction that actually works.

This repo has three parts:

| Part | What it is | Where |
|------|------------|-------|
| **Engine** | The two-layer detector + judge, integrations (LiteLLM, Cerebras), and a CLI. | [`loopguard/`](loopguard/) |
| **Cloud server** | A FastAPI + WebSocket service that runs real guarded agents and streams them. | [`loopguard/src/loopguard/server/`](loopguard/src/loopguard/server/) |
| **Mobile app** | An Expo "mission control" app to launch agents, watch them, and intervene from your phone. | [`cloud-app/`](cloud-app/) |

## The idea in 30 seconds

You give a real LLM agent a real repo and a task. Sometimes it gets stuck — retrying the same
failing tool call with tiny variations until your bill climbs. LoopGuard sits in the agent loop:

1. **Layer 1 (deterministic, free):** normalizes each step and flags exact repeats, semantic
   near-duplicates, A-B-A-B ping-pong between agents, or budget runaway.
2. **Layer 2 (LLM judge, opt-in):** when Layer 1 flags a candidate, a model decides if the
   agent is really stuck and — knowing which files actually exist in the repo — names the exact
   fix (e.g. *"this is a Python project; read `pyproject.toml`, not `package.json`"*).
3. **You choose what happens:** in **auto** mode the guard injects the fix and the agent
   recovers on its own; in **flag** mode it pauses and asks you — **terminate**, **continue
   once**, **allow** (stop flagging that tool, added to an allowlist you can review), or
   **prompt** (inject your own correction). Same four actions in the terminal and in the app.

## Demo projects (real agents, nothing scripted)

The loops are **not hardcoded**. Each demo project is a real workspace plus an agent whose
*system prompt* points it at the wrong well-known file, while the task is genuinely solvable by
reading a file that *is* there. A real Cerebras agent loops on its wrong assumption; the judge
redirects it to the real file; it recovers. Swap in your own repo and task and it still works.

```text
loopguard run npm-manifest --mode auto    # npm-minded agent loops on package.json -> judge -> pyproject.toml
loopguard run config-hunt   --mode flag   # insists on settings.yaml -> judge -> config.json (you approve)
loopguard run two-agent-manifest --mode auto   # two agents ping-pong, guard catches the oscillation
loopguard projects                        # list them all
```

## Quickstart

```bash
# 1. Engine + server
cd loopguard
python -m venv .venv && source .venv/bin/activate
pip install -e ".[server,cerebras]"        # or ".[server,litellm]" for any provider
echo "CEREBRAS_API_KEY=sk-..." > .env       # the model the agent + judge use

# 2. Try it in the terminal
loopguard run npm-manifest --mode auto

# 3. Or run the server + app
loopguard serve --port 8000
cd ../cloud-app && npm install && npx expo start   # press w for web, i for iOS, or scan in Expo Go
```

See [`loopguard/README.md`](loopguard/README.md) for the engine and CLI, and
[`cloud-app/README.md`](cloud-app/README.md) for the app.

## Status

- Engine + server: **71 tests passing** (`cd loopguard && pytest`).
- App: typechecks (`tsc --noEmit`), reducer unit tests (`npm test`), and the web bundle builds
  (`npx expo export --platform web`). The native screens are verified by running the app.
- Verified end-to-end against the real Cerebras API: agents genuinely loop, the judge fires,
  auto-fix recovers, and flag-mode terminate / continue / allow / prompt all work.
