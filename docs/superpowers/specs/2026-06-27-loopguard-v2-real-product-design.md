# LoopGuard v2 — From Showcase to Real Product

**Date:** 2026-06-27
**Status:** Approved design, pre-implementation
**Goal:** Replace every hardcoded/synthetic part of LoopGuard with genuinely working
behavior, so it is a real, usable circuit breaker for AI agents — not a staged demo.

---

## 1. Background & problem

LoopGuard's detection engine is already real: deterministic loop detection via local
hashing embeddings + cosine similarity, exact-signature matching, ping-pong detection,
and budget counting. That part stays.

What is faked today and must become real:

| Faked today | File | Becomes |
|---|---|---|
| Fake filesystem — `read_file` always fails | `cerebras_demo.py`, `demos.py` | A real `read_file` tool that reads actual disk |
| Hardcoded cost (`0.0004 * step`, `0.004 + i*0.005`) | `cerebras_demo.py`, `demos.py` | Real cost = real token usage × real price (`pricing.py`) |
| Hardcoded tokens (`120`) | `demos.py` | Real `usage` from the model response |
| Scripted correction ("read pyproject.toml") | `demos.py`, `cerebras_demo.py` | A real, LLM-generated correction (Layer 2 judge) |
| Cerebras-only, hardwired | `cerebras_client.py` | Provider-agnostic (OpenAI / Anthropic / Cerebras / local) |
| Unverified LiteLLM callback | `litellm_callback.py` | A real, tested `litellm.CustomLogger` capturing real tokens + cost |

## 2. Core architecture: two layers

### Layer 1 — deterministic detectors (keep, always on, free, offline)
The existing `detectors/` (exact, semantic, ping-pong, budget). Same input → same output,
no API key required. Role: **flag a suspected loop** (the cheap tripwire). Unchanged in
behavior; they remain the foundation and the only thing needed for offline mode.

### Layer 2 — LLM judge (new, optional)
When Layer 1 flags a loop **and** a provider is configured, the guard consults a real LLM.

- Input: the matching events (normalized + raw), the agent's task/goal, and the detector
  that fired.
- Output (structured): `{ is_loop: bool, reasoning: str, suggested_correction: str | null,
  confidence: float }`.
- If `is_loop == false` → **suppress the false positive**, allow the agent to continue.
- If `is_loop == true` → proceed to the configured action, carrying the **real,
  generated-for-this-problem** `suggested_correction`.

**No recursion:** the judge calls the provider *directly*, never through `guard.observe()`,
so judging cannot trip the guard on itself. The judge's own token cost IS tracked (added to
the run summary as a `judge` meta-cost).

The judge degrades gracefully: if the provider call fails or returns unparseable output,
fall back to "treat Layer 1's decision as final" (fail-safe, never fail-open silently).

## 3. Provider abstraction (works with any model)

A thin protocol decouples the judge (and live agent) from any specific vendor.

```python
class LLMProvider(Protocol):
    model: str
    def complete(self, messages: list[dict], *, tools=None, **kw) -> LLMResult: ...

class LLMResult:  # normalized across providers
    text: str
    tool_calls: list | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    raw: object       # original response, for cost extraction
```

Implementations:

1. **`LiteLLMProvider` (default, universal).** Wraps `litellm.completion(model=...)`.
   `model` uses LiteLLM routing strings: `cerebras/gpt-oss-120b`, `openai/gpt-4o`,
   `anthropic/claude-...`, `ollama/llama3`, etc. One code path → every provider, including
   local models. Real token usage from the response; real cost via `litellm.completion_cost`.
2. **`CerebrasProvider` (direct SDK).** Wraps the existing `CerebrasLLMClient` for
   minimal-deps / no-litellm runs. Cost via the static pricing table.

Selection: `--model` (+ optional `--provider`) on the CLI; config field `provider`/`model`;
API keys read from the environment per provider. **The demo defaults to Cerebras; any other
model is one flag away.**

## 4. Real cost (`pricing.py`)

- `cost_for(model, prompt_tokens, completion_tokens) -> float`.
- Order of precedence: (1) `litellm.completion_cost()` when the result came through LiteLLM
  and the model is known; (2) a built-in static `$/1M tokens` table for common models
  (Cerebras `gpt-oss-120b`, OpenAI `gpt-4o`/`gpt-4o-mini`, Anthropic Claude tiers); (3) if the
  model is unknown, cost is `0.0` and a one-time warning is logged (never invent a number).
- All `cost_usd` on events is computed this way. No hardcoded cost formulas remain.
- Offline (no LLM) runs are honestly `$0.00`.

## 5. Real tools (replace the fake filesystem)

- A real `read_file(path)` tool that reads the actual disk, returning file contents or a real
  "not found / not readable" error.
- The **live demo** points a "find the npm `package.json`" agent at a directory that genuinely
  has no `package.json` (the LoopGuard repo itself — a Python project). The agent really loops
  because the file genuinely is not there. Layer 2 really reasons "this is a Python project;
  read `pyproject.toml`," which really exists and is really read → genuine recovery. Nothing
  staged; the loop and the fix are both real.

## 6. Action modes (decision vs. response, decoupled)

The engine returns structured `LoopDecision`s; *what to do* about a confirmed loop is a policy,
selected by `--mode` / config `action`:

- **`pause`** — interactive local CLI: show the warning + judge verdict, prompt the human
  (terminate / continue / allowlist / inject). Today's behavior.
- **`flag`** — detect + report, **non-blocking**: surface the loop and the suggested fix in a
  structured form, let execution continue / hand off to a UI. (For dashboards and the future
  cloud app.)
- **`auto`** — **auto-repair**: when Layer 2 produced a `suggested_correction`, inject it and
  continue automatically with no human in the loop. (`raise`/`warn` remain available as before.)

**UI/transport stays out of the core.** `guard.observe()` and the detectors/judge contain no
terminal code; the Rich terminal UI is one front-end that consumes `LoopDecision`s. This keeps
the engine ready to be wrapped by other front-ends.

## 7. Offline mode (no key, CI-friendly)

`loopguard demo` (no `--live`) runs a real retry loop against the **real `read_file` tool**
pointed at paths that genuinely miss on disk. Real events, real Layer-1 detection, honest
`$0.00`, no provider. Layer 2 is skipped when no provider is configured. This path runs in CI
with no secrets.

## 8. Real integration: guard your own agent

`litellm_callback.py` becomes a proper `litellm.integrations.custom_logger.CustomLogger`:
- `log_success_event` / `log_failure_event` extract **real tokens and real cost** from the
  response and feed `guard.observe()`.
- Register once (`litellm.callbacks = [LoopGuardLiteLLMCallback(guard)]`) and any LiteLLM-based
  agent is guarded automatically.
- Ships with a runnable example and a unit test (mocked litellm response objects).

`wrap_tool()` and manual `guard.observe()` remain the other two real entry points.

## 9. CLI surface

```
loopguard demo                                  # offline deterministic, no key, real failing tool
loopguard demo --live                           # real agent, Cerebras default, Layer 1 + Layer 2
loopguard demo --live --model openai/gpt-4o     # any provider/model
loopguard demo --live --mode auto               # auto-repair (no human)
loopguard demo --live --mode flag               # detect + report, non-blocking
loopguard inspect runs/last.jsonl               # Rich table (already real)
loopguard init-config                           # write default config
```

## 10. Testing

New/updated tests (all key-free tests run in CI):
- Judge with a **mocked provider**: confirmed-loop verdict, false-positive-suppression verdict,
  unparseable-output fallback.
- Pricing math (known model, unknown model → 0 + warning).
- Provider routing (`LiteLLMProvider` and `CerebrasProvider` mocked).
- Real-tool retry loop trips Layer 1 on genuinely-missing files.
- Action modes: `auto` injects the correction and recovers; `flag` is non-blocking; `pause`
  honored (mocked prompt).
- LiteLLM callback feeds real tokens/cost into the guard (mocked response objects).
- All 13 existing tests stay green.

## 11. Dependencies

- Add `litellm` as an optional extra: `loopguard[litellm]` (default universal provider).
- Keep `cerebras` extra for the direct SDK path.
- Core install stays light (no LLM deps required for offline mode).

## 12. Out of scope (this build)

- A general "run any arbitrary agent framework" orchestrator — the LiteLLM callback is the real
  "guard any agent" path; we do not invent our own agent framework.
- The **cloud service + React Native app** is a documented future phase. The two-layer engine,
  structured `LoopDecision`s, and the `flag`/`auto` modes are designed so the same engine can be
  wrapped by a cloud API + RN front-end later, but none of that is built now.

## 13. Future phase (recorded, not built)

- Cloud mode: the engine behind an API; `flag` mode streams detections to a dashboard, `auto`
  mode performs server-side auto-repair.
- React Native app as the cloud front-end (the terminal UI's counterpart), consuming the same
  `LoopDecision` stream.
