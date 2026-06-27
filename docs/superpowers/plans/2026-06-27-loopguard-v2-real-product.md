# LoopGuard v2 (Real Product) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every hardcoded/synthetic part of LoopGuard with genuinely working behavior — provider-agnostic LLM judging, real cost, real filesystem tools, real corrections, and `pause`/`flag`/`auto` action modes.

**Architecture:** Layer 1 = existing deterministic detectors (flag suspected loops, free, offline). Layer 2 = optional `LLMJudge` that, via a pluggable `LLMProvider`, reasons about a flagged loop and writes a real correction. The guard wires Layer 2 into `observe()` and applies an action policy. A real minimal tool-calling agent (`run_agent`) drives the live demo against real files; a real no-LLM retry loop drives the offline demo.

**Tech Stack:** Python 3.10+, pydantic v2, scikit-learn (existing), numpy (existing), typer + rich (existing), litellm (new optional extra), cerebras_cloud_sdk (existing optional extra), pytest.

## Global Constraints

- Package layout: source under `src/loopguard/`, tests under `tests/` (pytest `pythonpath = ["src"]`). Verbatim from `pyproject.toml`.
- Tests import helpers directly from `conftest` (e.g. `from conftest import tool_event`) — match existing style.
- ruff line-length = 100.
- Run command for a single test: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest <path>::<name> -v`.
- Full suite: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/ -q`.
- Reinstall editable after structural changes: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pip install -e ".[dev]" -q`.
- No hardcoded cost or token numbers anywhere outside `pricing.py`'s static table.
- The core engine (`detectors/`, `judge.py`, `guard.py`, `providers/`) MUST contain no terminal/print code. Only `ui/terminal.py`, `cli.py`, and the demo modules may print.
- Every committed state keeps the full suite green.
- All tests that need no API key must pass without network. Provider/judge tests use mocks.
- Commit message footer for every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Kegf7sKtfWgJ93DkThr1Lk
  ```

## File Structure

**Create:**
- `src/loopguard/pricing.py` — model → $/token table; `cost_for()`.
- `src/loopguard/providers/__init__.py` — `make_provider()` factory; re-exports.
- `src/loopguard/providers/base.py` — `LLMProvider` Protocol; `LLMResult` dataclass.
- `src/loopguard/providers/litellm_provider.py` — `LiteLLMProvider`.
- `src/loopguard/providers/cerebras_provider.py` — `CerebrasProvider`.
- `src/loopguard/judge.py` — `JudgeVerdict`, `LLMJudge`.
- `src/loopguard/tools.py` — real `read_file` tool + `READ_FILE_SCHEMA` + `TOOLS`.
- `src/loopguard/agent.py` — `run_agent()`, `AgentResult`.
- `src/loopguard/live_demo.py` — provider-agnostic live agent demo (replaces `cerebras_demo.py`).
- Tests: `tests/test_pricing.py`, `tests/test_providers.py`, `tests/test_judge.py`, `tests/test_tools.py`, `tests/test_agent.py`, `tests/test_action_modes.py`, `tests/test_litellm_callback.py`.

**Modify:**
- `src/loopguard/config.py` — add `flag`/`auto` to `action`; add `enable_judge`.
- `src/loopguard/decision.py` — add `judged`, `judge_reasoning`, `judge_confidence`.
- `src/loopguard/guard.py` — accept a judge; `observe(event, task=None)`; Layer-2 integration; action modes; judge-cost tracking.
- `src/loopguard/ui/terminal.py` — render judge verdict; non-interactive paths for `flag`/`auto`.
- `src/loopguard/demos.py` — offline demo uses the real `read_file` tool.
- `src/loopguard/cli.py` — `--live`, `--model`, `--provider`, `--mode`.
- `src/loopguard/integrations/litellm_callback.py` — real `CustomLogger` with tokens+cost.
- `src/loopguard/__init__.py` — export new public symbols.
- `pyproject.toml` — add `litellm` optional extra.
- `README.md` — honest description of two layers, providers, modes, offline.

**Delete:**
- `src/loopguard/cerebras_demo.py` — superseded by `live_demo.py` (after Task 10).

---

### Task 1: Real pricing (`pricing.py`)

**Files:**
- Create: `src/loopguard/pricing.py`
- Test: `tests/test_pricing.py`

**Interfaces:**
- Produces: `cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float`; `PRICING: dict[str, tuple[float, float]]` ($/1M input, $/1M output).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pricing.py
from loopguard.pricing import cost_for


def test_known_model_cost_is_token_weighted():
    # gpt-oss-120b = ($0.25/M in, $0.69/M out)
    cost = cost_for("cerebras/gpt-oss-120b", 1_000_000, 1_000_000)
    assert round(cost, 4) == round(0.25 + 0.69, 4)


def test_bare_model_name_resolves_same_as_prefixed():
    assert cost_for("gpt-oss-120b", 2000, 1000) == cost_for("cerebras/gpt-oss-120b", 2000, 1000)


def test_unknown_model_returns_zero():
    assert cost_for("totally-made-up-model", 5000, 5000) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_pricing.py -v`
Expected: FAIL (`ModuleNotFoundError: loopguard.pricing`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/loopguard/pricing.py
from __future__ import annotations

import logging

_LOG = logging.getLogger("loopguard.pricing")

# $ per 1,000,000 tokens: (input, output). Extend as needed.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-oss-120b": (0.25, 0.69),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-3-5-sonnet-latest": (3.00, 15.00),
    "claude-3-5-haiku-latest": (0.80, 4.00),
}

_WARNED: set[str] = set()


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    key = model.lower()
    price = PRICING.get(key) or PRICING.get(key.split("/")[-1])
    if price is None:
        if model not in _WARNED:
            _LOG.warning("No pricing entry for model %r; reporting $0.00", model)
            _WARNED.add(model)
        return 0.0
    price_in, price_out = price
    return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_pricing.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/pricing.py loopguard/tests/test_pricing.py && git commit -m "feat(pricing): real per-model cost from token usage"
```

---

### Task 2: Provider abstraction (`providers/`)

**Files:**
- Create: `src/loopguard/providers/__init__.py`, `src/loopguard/providers/base.py`, `src/loopguard/providers/litellm_provider.py`, `src/loopguard/providers/cerebras_provider.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: `cost_for` (Task 1).
- Produces:
  - `LLMResult` dataclass: `text: str`, `tool_calls: list`, `prompt_tokens: int`, `completion_tokens: int`, `total_tokens: int`, `cost_usd: float`, `raw: Any`.
  - `LLMProvider` Protocol: attribute `model: str`; method `complete(messages: list[dict], *, tools=None, temperature=0.2, max_tokens=512) -> LLMResult`.
  - `make_provider(model: str = "cerebras/gpt-oss-120b", provider: str = "auto") -> LLMProvider`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_providers.py
from loopguard.providers.base import LLMResult, LLMProvider


class _FakeProvider:
    model = "fake/model"

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512):
        return LLMResult(text="ok", prompt_tokens=10, completion_tokens=5, total_tokens=15)


def test_llmresult_defaults():
    r = LLMResult(text="hi")
    assert r.tool_calls == [] and r.cost_usd == 0.0 and r.total_tokens == 0


def test_fake_satisfies_protocol():
    p = _FakeProvider()
    assert isinstance(p, LLMProvider)
    out = p.complete([{"role": "user", "content": "x"}])
    assert out.text == "ok" and out.total_tokens == 15


def test_litellm_provider_normalizes_response(monkeypatch):
    from loopguard.providers.litellm_provider import LiteLLMProvider

    class _Msg:
        content = "hello"
        tool_calls = []

    class _Usage:
        prompt_tokens = 100
        completion_tokens = 20
        total_tokens = 120

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]
        usage = _Usage()

    import loopguard.providers.litellm_provider as mod

    fake_litellm = type("L", (), {
        "completion": staticmethod(lambda **kw: _Resp()),
        "completion_cost": staticmethod(lambda **kw: 0.0012),
    })()
    monkeypatch.setattr(mod, "_import_litellm", lambda: fake_litellm)
    p = LiteLLMProvider("openai/gpt-4o")
    out = p.complete([{"role": "user", "content": "hi"}])
    assert out.text == "hello"
    assert out.prompt_tokens == 100 and out.completion_tokens == 20
    assert out.cost_usd == 0.0012
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_providers.py -v`
Expected: FAIL (`ModuleNotFoundError: loopguard.providers`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/loopguard/providers/base.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class LLMResult:
    text: str
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    raw: Any = None


@runtime_checkable
class LLMProvider(Protocol):
    model: str

    def complete(
        self,
        messages: list[dict],
        *,
        tools: list | None = None,
        temperature: float = 0.2,
        max_tokens: int = 512,
    ) -> LLMResult: ...
```

```python
# src/loopguard/providers/litellm_provider.py
from __future__ import annotations

from ..pricing import cost_for
from .base import LLMResult


def _import_litellm():
    try:
        import litellm
    except ImportError as exc:  # pragma: no cover - exercised via make_provider
        raise RuntimeError('Install litellm support with: pip install "loopguard[litellm]"') from exc
    return litellm


class LiteLLMProvider:
    def __init__(self, model: str):
        self.model = model
        self._litellm = _import_litellm()

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512) -> LLMResult:
        resp = self._litellm.completion(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        msg = resp.choices[0].message
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        tt = int(getattr(usage, "total_tokens", 0) or (pt + ct))
        cost = 0.0
        try:
            cost = float(self._litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:
            cost = 0.0
        if not cost:
            cost = cost_for(self.model, pt, ct)
        return LLMResult(
            text=getattr(msg, "content", "") or "",
            tool_calls=list(getattr(msg, "tool_calls", None) or []),
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            cost_usd=cost,
            raw=resp,
        )
```

```python
# src/loopguard/providers/cerebras_provider.py
from __future__ import annotations

from ..pricing import cost_for
from .base import LLMResult


class CerebrasProvider:
    def __init__(self, model: str | None = None):
        from ..integrations.cerebras_client import CerebrasLLMClient

        self._client = CerebrasLLMClient(model=model)
        self.model = self._client.model

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512) -> LLMResult:
        resp = self._client.chat(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        )
        msg = resp.choices[0].message
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        tt = int(getattr(usage, "total_tokens", 0) or (pt + ct))
        return LLMResult(
            text=getattr(msg, "content", "") or "",
            tool_calls=list(getattr(msg, "tool_calls", None) or []),
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            cost_usd=cost_for(self.model, pt, ct),
            raw=resp,
        )
```

```python
# src/loopguard/providers/__init__.py
from __future__ import annotations

from .base import LLMProvider, LLMResult


def make_provider(model: str = "cerebras/gpt-oss-120b", provider: str = "auto") -> LLMProvider:
    """Build a provider. provider in {"auto","litellm","cerebras"}.

    "auto": prefer LiteLLM (universal); if litellm is not installed, fall back to the
    direct Cerebras SDK when the model is a cerebras model.
    """
    if provider == "cerebras":
        from .cerebras_provider import CerebrasProvider

        return CerebrasProvider(model=model.split("/")[-1])
    if provider == "litellm":
        from .litellm_provider import LiteLLMProvider

        return LiteLLMProvider(model=model)
    if provider == "auto":
        try:
            from .litellm_provider import LiteLLMProvider

            return LiteLLMProvider(model=model)
        except RuntimeError:
            from .cerebras_provider import CerebrasProvider

            return CerebrasProvider(model=model.split("/")[-1])
    raise ValueError(f"Unknown provider {provider!r}")


__all__ = ["LLMProvider", "LLMResult", "make_provider"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_providers.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/providers loopguard/tests/test_providers.py && git commit -m "feat(providers): provider-agnostic LLM interface (litellm + cerebras)"
```

---

### Task 3: LLM judge (`judge.py`)

**Files:**
- Create: `src/loopguard/judge.py`
- Test: `tests/test_judge.py`

**Interfaces:**
- Consumes: `LLMProvider`/`LLMResult` (Task 2), `normalize_event` (existing), `LoopEvent` (existing).
- Produces:
  - `JudgeVerdict` (pydantic): `is_loop: bool`, `reasoning: str = ""`, `suggested_correction: str | None = None`, `confidence: float = 0.5`, `cost_usd: float = 0.0`.
  - `LLMJudge(provider)`; `.judge(events: list[LoopEvent], task: str | None = None, detector: str | None = None) -> JudgeVerdict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge.py
from loopguard.judge import JudgeVerdict, LLMJudge
from loopguard.providers.base import LLMResult

from conftest import tool_event


class _ScriptedProvider:
    model = "fake/model"

    def __init__(self, text):
        self._text = text

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512):
        return LLMResult(text=self._text, prompt_tokens=50, completion_tokens=20, cost_usd=0.0007)


def test_judge_confirms_loop_and_extracts_correction():
    provider = _ScriptedProvider(
        '{"is_loop": true, "reasoning": "same file repeatedly",'
        ' "suggested_correction": "Read pyproject.toml instead", "confidence": 0.9}'
    )
    verdict = LLMJudge(provider).judge([tool_event(), tool_event()], task="find package.json")
    assert verdict.is_loop is True
    assert verdict.suggested_correction == "Read pyproject.toml instead"
    assert verdict.confidence == 0.9
    assert verdict.cost_usd == 0.0007


def test_judge_suppresses_false_positive():
    provider = _ScriptedProvider(
        'Here is my answer: {"is_loop": false, "reasoning": "making progress",'
        ' "suggested_correction": null, "confidence": 0.8}'
    )
    verdict = LLMJudge(provider).judge([tool_event()], task="t")
    assert verdict.is_loop is False
    assert verdict.suggested_correction is None


def test_judge_unparseable_defers_to_detector():
    verdict = LLMJudge(_ScriptedProvider("not json at all")).judge([tool_event()])
    assert verdict.is_loop is True  # fail-safe: defer to Layer 1
    assert verdict.confidence == 0.0


def test_judge_provider_error_defers_to_detector():
    class _Boom:
        model = "x"

        def complete(self, *a, **k):
            raise RuntimeError("network down")

    verdict = LLMJudge(_Boom()).judge([tool_event()])
    assert verdict.is_loop is True
    assert verdict.confidence == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_judge.py -v`
Expected: FAIL (`ModuleNotFoundError: loopguard.judge`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/loopguard/judge.py
from __future__ import annotations

import json

from pydantic import BaseModel

from .event import LoopEvent
from .normalize import normalize_event
from .providers.base import LLMProvider

_JUDGE_SYSTEM = (
    "You are LoopGuard's loop judge. A deterministic detector flagged an AI agent's recent "
    "actions as a possible repetitive loop. Decide whether the agent is genuinely STUCK "
    "(repeating without progress) or actually making progress. If stuck, write a SPECIFIC, "
    "actionable correction telling the agent exactly what to do differently to break out. "
    'Respond with ONLY a JSON object: {"is_loop": boolean, "reasoning": string, '
    '"suggested_correction": string or null, "confidence": number between 0 and 1}.'
)


class JudgeVerdict(BaseModel):
    is_loop: bool
    reasoning: str = ""
    suggested_correction: str | None = None
    confidence: float = 0.5
    cost_usd: float = 0.0


def _defer(reason: str) -> JudgeVerdict:
    # Fail-safe: when the judge cannot decide, defer to Layer 1 (treat as a loop).
    return JudgeVerdict(is_loop=True, reasoning=reason, confidence=0.0)


class LLMJudge:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def judge(
        self,
        events: list[LoopEvent],
        task: str | None = None,
        detector: str | None = None,
    ) -> JudgeVerdict:
        prompt = self._render(events, task, detector)
        try:
            result = self.provider.complete(
                [
                    {"role": "system", "content": _JUDGE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=400,
            )
        except Exception as exc:  # noqa: BLE001 - any provider failure must fail safe
            return _defer(f"judge unavailable ({exc}); deferring to detector")
        verdict = self._parse(result.text)
        verdict.cost_usd = result.cost_usd
        return verdict

    @staticmethod
    def _render(events: list[LoopEvent], task: str | None, detector: str | None) -> str:
        lines: list[str] = []
        if task:
            lines.append(f"Agent task: {task}")
        if detector:
            lines.append(f"Detector that fired: {detector}")
        lines.append("Recent actions (normalized):")
        for i, e in enumerate(events, 1):
            lines.append(f"{i}. {normalize_event(e)}")
        return "\n".join(lines)

    @staticmethod
    def _parse(text: str) -> JudgeVerdict:
        raw = (text or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return JudgeVerdict(
                    is_loop=bool(data.get("is_loop", True)),
                    reasoning=str(data.get("reasoning", "")),
                    suggested_correction=(data.get("suggested_correction") or None),
                    confidence=float(data.get("confidence", 0.5)),
                )
            except Exception:  # noqa: BLE001 - malformed JSON falls through to defer
                pass
        return _defer("unparseable judge output; deferring to detector")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_judge.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/judge.py loopguard/tests/test_judge.py && git commit -m "feat(judge): Layer-2 LLM judge with fail-safe JSON parsing"
```

---

### Task 4: Real filesystem tool (`tools.py`)

**Files:**
- Create: `src/loopguard/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces:
  - `read_file(path: str, *, root: str | Path = ".") -> str` — real disk read; returns contents (truncated to 4000 chars) or a string starting with `"Error:"`.
  - `READ_FILE_SCHEMA: dict` — OpenAI/LiteLLM tool schema for `read_file`.
  - `TOOLS: dict[str, Callable[..., str]]` — name → impl, currently `{"read_file": read_file}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools.py
from loopguard.tools import READ_FILE_SCHEMA, TOOLS, read_file


def test_read_existing_file(tmp_path):
    (tmp_path / "hello.txt").write_text("hi there")
    assert read_file("hello.txt", root=tmp_path) == "hi there"


def test_missing_file_returns_error(tmp_path):
    out = read_file("package.json", root=tmp_path)
    assert out.startswith("Error:") and "not found" in out


def test_path_escape_is_blocked(tmp_path):
    out = read_file("../../etc/passwd", root=tmp_path)
    assert out.startswith("Error:") and "escapes" in out


def test_schema_and_registry_shapes():
    assert READ_FILE_SCHEMA["function"]["name"] == "read_file"
    assert "read_file" in TOOLS and callable(TOOLS["read_file"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_tools.py -v`
Expected: FAIL (`ModuleNotFoundError: loopguard.tools`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/loopguard/tools.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

READ_FILE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file from disk and return its contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filesystem path to read."}
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}


def read_file(path: str, *, root: str | Path = ".") -> str:
    base = Path(root).resolve()
    target = (base / path).resolve()
    if base != target and base not in target.parents:
        return f"Error: path {path!r} escapes the allowed directory."
    if not target.exists():
        return f"Error: {path} not found."
    if target.is_dir():
        return f"Error: {path} is a directory, not a file."
    try:
        return target.read_text(encoding="utf-8")[:4000]
    except Exception as exc:  # noqa: BLE001 - surface real read failures to the agent
        return f"Error: could not read {path}: {exc}"


TOOLS: dict[str, Callable[..., str]] = {"read_file": read_file}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_tools.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/tools.py loopguard/tests/test_tools.py && git commit -m "feat(tools): real read_file tool with directory containment"
```

---

### Task 5: Config + decision additions

**Files:**
- Modify: `src/loopguard/config.py`, `src/loopguard/decision.py`
- Test: `tests/test_config_decision.py` (create)

**Interfaces:**
- Produces:
  - `LoopGuardConfig.action: Literal["pause","raise","warn","flag","auto"]` (default `"pause"`).
  - `LoopGuardConfig.enable_judge: bool = True`.
  - `LoopDecision.judged: bool = False`, `LoopDecision.judge_reasoning: str | None = None`, `LoopDecision.judge_confidence: float | None = None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_decision.py
from loopguard.config import LoopGuardConfig
from loopguard.decision import LoopDecision


def test_new_action_modes_allowed():
    for mode in ["pause", "raise", "warn", "flag", "auto"]:
        assert LoopGuardConfig(action=mode).action == mode


def test_enable_judge_defaults_true():
    assert LoopGuardConfig().enable_judge is True


def test_decision_has_judge_fields():
    d = LoopDecision(judged=True, judge_reasoning="stuck", judge_confidence=0.9)
    assert d.judged is True and d.judge_reasoning == "stuck" and d.judge_confidence == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_config_decision.py -v`
Expected: FAIL (validation error on `action="flag"` / unknown field `judged`).

- [ ] **Step 3: Write minimal implementation**

In `src/loopguard/config.py`, change the `action` field line:
```python
    action: Literal["pause", "raise", "warn", "flag", "auto"] = "pause"
```
and add after it:
```python
    enable_judge: bool = True
```

In `src/loopguard/decision.py`, add these fields to `LoopDecision` (after `suggested_message`):
```python
    judged: bool = False
    judge_reasoning: str | None = None
    judge_confidence: float | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_config_decision.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/config.py loopguard/src/loopguard/decision.py loopguard/tests/test_config_decision.py && git commit -m "feat(config): add flag/auto modes, enable_judge, and judge decision fields"
```

---

### Task 6: Guard ⇄ judge integration + action modes

**Files:**
- Modify: `src/loopguard/guard.py`, `src/loopguard/ui/terminal.py`
- Test: `tests/test_action_modes.py`

**Interfaces:**
- Consumes: `LLMJudge`/`JudgeVerdict` (Task 3), config/decision fields (Task 5).
- Produces:
  - `LoopGuard(config=None, judge: LLMJudge | None = None)`.
  - `LoopGuard.observe(event: LoopEvent, task: str | None = None) -> LoopDecision`.
  - `LoopGuard.summary()` includes key `"judge_cost_usd": float`.
  - `ui.terminal.apply_flag(decision)` and `ui.terminal.apply_auto(decision)` (non-interactive policies).

**Behavior:** When a Layer-1 detector trips: if `config.enable_judge` and a judge is set, call `judge.judge(matching_events, task, detector)`. Add `verdict.cost_usd` to judge-cost total. If `verdict.is_loop is False`, return an allowed, non-tripped decision (`reason="judge: false positive suppressed"`, `judged=True`). Otherwise set `decision.judged=True`, `decision.judge_reasoning`, `decision.judge_confidence`, and if `verdict.suggested_correction`, set `decision.suggested_message`; then dispatch on action mode.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_action_modes.py
from loopguard import LoopGuard, LoopGuardConfig
from loopguard.judge import JudgeVerdict

from conftest import tool_event


class _StubJudge:
    def __init__(self, verdict):
        self._verdict = verdict
        self.calls = 0

    def judge(self, events, task=None, detector=None):
        self.calls += 1
        return self._verdict


def _trip(guard):
    d = None
    for path in ["./package.json", "package.json", "/app/package.json"]:
        d = guard.observe(tool_event(path), task="find package.json")
    return d


def test_judge_false_positive_is_suppressed():
    judge = _StubJudge(JudgeVerdict(is_loop=False, reasoning="progress"))
    guard = LoopGuard(
        LoopGuardConfig(action="flag", enable_exact=False, enable_budget=False), judge=judge
    )
    d = _trip(guard)
    assert judge.calls == 1
    assert d.tripped is False and d.allowed is True
    assert d.judged is True


def test_auto_mode_injects_correction():
    judge = _StubJudge(
        JudgeVerdict(is_loop=True, reasoning="stuck", suggested_correction="read pyproject.toml")
    )
    guard = LoopGuard(
        LoopGuardConfig(action="auto", enable_exact=False, enable_budget=False), judge=judge
    )
    d = _trip(guard)
    assert d.developer_action == "inject"
    assert d.allowed is True
    assert d.suggested_message == "read pyproject.toml"
    assert d.judge_reasoning == "stuck"


def test_flag_mode_is_non_blocking():
    judge = _StubJudge(JudgeVerdict(is_loop=True, reasoning="stuck"))
    guard = LoopGuard(
        LoopGuardConfig(action="flag", enable_exact=False, enable_budget=False), judge=judge
    )
    d = _trip(guard)
    assert d.tripped is True and d.allowed is True
    assert d.developer_action == "none"


def test_summary_tracks_judge_cost():
    judge = _StubJudge(JudgeVerdict(is_loop=True, reasoning="stuck", cost_usd=0.002))
    guard = LoopGuard(
        LoopGuardConfig(action="flag", enable_exact=False, enable_budget=False), judge=judge
    )
    _trip(guard)
    assert guard.summary()["judge_cost_usd"] == 0.002
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_action_modes.py -v`
Expected: FAIL (`observe()` has no `task` kwarg / `LoopGuard` has no `judge` param).

- [ ] **Step 3: Write minimal implementation**

Rewrite `src/loopguard/guard.py` `__init__`, `observe`, and `_handle`, and add helpers. Full new content for those parts:

```python
# in src/loopguard/guard.py

    def __init__(self, config: LoopGuardConfig | None = None, judge=None):
        self.config = config or LoopGuardConfig()
        self.judge = judge
        self._events: dict[str, deque[LoopEvent]] = defaultdict(
            lambda: deque(maxlen=self.config.window_size)
        )
        self._all: list[LoopEvent] = []
        self._allowlisted: set[str] = set(self.config.allowlisted_tools)
        self._judge_cost: float = 0.0

    def observe(self, event: LoopEvent, task: str | None = None) -> LoopDecision:
        self._events[event.run_id].append(event)
        self._all.append(event)
        events = list(self._events[event.run_id])
        if event.tool_name in self._allowlisted:
            return LoopDecision(reason="allowlisted")
        for enabled, detector in [
            (self.config.enable_budget, lambda: budget.detect(events, self.config)),
            (self.config.enable_exact, lambda: exact.detect(events, self.config)),
            (self.config.enable_pingpong, lambda: pingpong.detect(events, self.config)),
            (self.config.enable_semantic, lambda: semantic.detect(events, self.config)),
        ]:
            if enabled:
                decision = detector()
                if decision.tripped:
                    decision = self._consult_judge(decision, task)
                    if not decision.tripped:
                        return decision
                    return self._handle(decision)
        return LoopDecision()

    def _consult_judge(self, decision: LoopDecision, task: str | None) -> LoopDecision:
        if not (self.config.enable_judge and self.judge is not None):
            return decision
        verdict = self.judge.judge(decision.matching_events, task=task, detector=decision.detector)
        self._judge_cost += verdict.cost_usd
        if not verdict.is_loop:
            return LoopDecision(
                allowed=True,
                tripped=False,
                reason="judge: false positive suppressed",
                detector=decision.detector,
                judged=True,
                judge_reasoning=verdict.reasoning,
                judge_confidence=verdict.confidence,
            )
        decision.judged = True
        decision.judge_reasoning = verdict.reasoning
        decision.judge_confidence = verdict.confidence
        if verdict.suggested_correction:
            decision.suggested_message = verdict.suggested_correction
        return decision

    def _handle(self, decision: LoopDecision) -> LoopDecision:
        if self.config.action == "raise":
            raise LoopDetectedError(decision)
        if self.config.action == "warn":
            show_warning(decision)
            decision.allowed = True
            return decision
        if self.config.action == "flag":
            return apply_flag(decision)
        if self.config.action == "auto":
            return apply_auto(decision)
        decision = pause_for_action(decision)
        if decision.developer_action == "allowlist":
            for e in decision.matching_events:
                if e.tool_name:
                    self._allowlisted.add(e.tool_name)
        return decision
```

Update the summary method to include judge cost:
```python
    def summary(self) -> dict[str, float | int]:
        return {
            "events": len(self._all),
            "tokens": sum(e.tokens for e in self._all),
            "cost_usd": sum(e.cost_usd for e in self._all),
            "judge_cost_usd": self._judge_cost,
        }
```

Update the import line in `guard.py`:
```python
from .ui.terminal import apply_auto, apply_flag, pause_for_action, show_warning
```

Add to `src/loopguard/ui/terminal.py`:
```python
def _render_verdict(decision: LoopDecision) -> None:
    if decision.judged and decision.judge_reasoning:
        conf = decision.judge_confidence if decision.judge_confidence is not None else 0.0
        console.print(f"[bold magenta]Judge:[/bold magenta] {decision.judge_reasoning} "
                      f"(confidence {conf:.2f})")
        if decision.suggested_message:
            console.print(f"[magenta]Suggested fix:[/magenta] {decision.suggested_message}")


def apply_flag(decision: LoopDecision) -> LoopDecision:
    show_warning(decision)
    _render_verdict(decision)
    console.print("[yellow]flag mode: reported, not blocking.[/yellow]")
    decision.allowed = True
    decision.developer_action = "none"
    return decision


def apply_auto(decision: LoopDecision) -> LoopDecision:
    show_warning(decision)
    _render_verdict(decision)
    if decision.suggested_message:
        decision.developer_action = "inject"
        decision.allowed = True
        console.print("[green]auto mode: injecting correction and continuing.[/green]")
    else:
        decision.developer_action = "terminate"
        decision.allowed = False
        console.print("[red]auto mode: no correction available; terminating.[/red]")
    return decision
```

Also call `_render_verdict(decision)` inside `pause_for_action` right after `show_warning(decision)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_action_modes.py tests/ -q`
Expected: PASS (all green, including existing tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/guard.py loopguard/src/loopguard/ui/terminal.py loopguard/tests/test_action_modes.py && git commit -m "feat(guard): Layer-2 judge integration + flag/auto action modes"
```

---

### Task 7: Real agent loop (`agent.py`)

**Files:**
- Create: `src/loopguard/agent.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: `LLMProvider`/`LLMResult` (Task 2), `LoopGuard` (Task 6), `LoopEvent` (existing).
- Produces:
  - `AgentResult` dataclass: `steps: int`, `final_text: str | None`, `stopped_by_guard: bool`.
  - `run_agent(provider, *, system, task, tools_schema, tool_impls, guard=None, run_id="agent", agent_name="agent", max_steps=14, on_event=None) -> AgentResult`.

**Notes:** Token/cost from a turn are attributed to the FIRST tool-call event of that turn (subsequent calls in the same turn get 0) to avoid double counting. The guard's decision controls flow: if `not decision.allowed` → stop (`stopped_by_guard=True`); if `decision.suggested_message` → append it as a user message before the next turn.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent.py
from loopguard import LoopGuard, LoopGuardConfig
from loopguard.agent import run_agent
from loopguard.providers.base import LLMResult
from loopguard.tools import READ_FILE_SCHEMA, TOOLS


class _ToolCall:
    def __init__(self, cid, name, args_json):
        self.id = cid
        self.type = "function"
        self.function = type("F", (), {"name": name, "arguments": args_json})()


class _LoopingProvider:
    """Always calls read_file on a missing file -> deterministic loop."""

    model = "fake/model"

    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512):
        self.calls += 1
        return LLMResult(
            text="",
            tool_calls=[_ToolCall(f"c{self.calls}", "read_file", '{"path": "package.json"}')],
            prompt_tokens=100,
            completion_tokens=10,
            total_tokens=110,
            cost_usd=0.001,
        )


def test_agent_loops_and_guard_stops_it(tmp_path):
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_budget=False))
    provider = _LoopingProvider()
    result = run_agent(
        provider,
        system="find package.json",
        task="find package.json",
        tools_schema=[READ_FILE_SCHEMA],
        tool_impls={"read_file": lambda path: TOOLS["read_file"](path, root=tmp_path)},
        guard=guard,
        max_steps=10,
    )
    # warn mode never blocks, so it runs to max_steps; events were recorded
    assert provider.calls == 10
    assert guard.summary()["events"] == 10


def test_agent_records_real_tokens_and_cost(tmp_path):
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    provider = _LoopingProvider()
    run_agent(
        provider,
        system="s",
        task="t",
        tools_schema=[READ_FILE_SCHEMA],
        tool_impls={"read_file": lambda path: TOOLS["read_file"](path, root=tmp_path)},
        guard=guard,
        max_steps=3,
    )
    s = guard.summary()
    assert s["tokens"] == 330  # 3 turns * 110
    assert round(s["cost_usd"], 4) == 0.003
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_agent.py -v`
Expected: FAIL (`ModuleNotFoundError: loopguard.agent`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/loopguard/agent.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .event import LoopEvent
from .guard import LoopGuard
from .providers.base import LLMProvider


@dataclass
class AgentResult:
    steps: int
    final_text: str | None
    stopped_by_guard: bool


def _tc_id(tc: Any) -> str:
    return getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else "call")


def _tc_name_args(tc: Any) -> tuple[str, dict]:
    fn = getattr(tc, "function", None)
    if fn is None and isinstance(tc, dict):
        fn = tc.get("function", {})
    name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else "tool")
    raw = getattr(fn, "arguments", None)
    if raw is None and isinstance(fn, dict):
        raw = fn.get("arguments", "{}")
    try:
        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:  # noqa: BLE001 - malformed tool args become empty
        args = {}
    return name, args


def _serialize_tc(tc: Any) -> dict:
    name, _ = _tc_name_args(tc)
    fn = getattr(tc, "function", None)
    raw = getattr(fn, "arguments", None) if fn is not None else "{}"
    if not isinstance(raw, str):
        raw = json.dumps(raw or {})
    return {"id": _tc_id(tc), "type": "function", "function": {"name": name, "arguments": raw}}


def run_agent(
    provider: LLMProvider,
    *,
    system: str,
    task: str,
    tools_schema: list[dict],
    tool_impls: dict[str, Callable[..., str]],
    guard: LoopGuard | None = None,
    run_id: str = "agent",
    agent_name: str = "agent",
    max_steps: int = 14,
    on_event: Callable[[int, str, dict, str], None] | None = None,
) -> AgentResult:
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    for step in range(1, max_steps + 1):
        result = provider.complete(messages, tools=tools_schema)
        entry: dict[str, Any] = {"role": "assistant", "content": result.text or ""}
        if result.tool_calls:
            entry["tool_calls"] = [_serialize_tc(tc) for tc in result.tool_calls]
        messages.append(entry)

        if not result.tool_calls:
            return AgentResult(step, result.text, False)

        correction: str | None = None
        first = True
        for tc in result.tool_calls:
            name, args = _tc_name_args(tc)
            impl = tool_impls.get(name)
            output = impl(**args) if impl else f"Error: unknown tool {name}"
            if on_event:
                on_event(step, name, args, output)
            messages.append({"role": "tool", "tool_call_id": _tc_id(tc), "content": output})
            is_err = output.startswith("Error:")
            if guard is not None:
                event = LoopEvent(
                    run_id=run_id,
                    agent=agent_name,
                    kind="tool_call",
                    tool_name=name,
                    tool_args=args,
                    output_text=None if is_err else output,
                    error=output if is_err else None,
                    tokens=result.total_tokens if first else 0,
                    cost_usd=result.cost_usd if first else 0.0,
                )
                decision = guard.observe(event, task=task)
                if not decision.allowed:
                    return AgentResult(step, None, True)
                if decision.suggested_message:
                    correction = decision.suggested_message
            first = False

        if correction:
            messages.append({"role": "user", "content": correction})
    return AgentResult(max_steps, None, False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_agent.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/agent.py loopguard/tests/test_agent.py && git commit -m "feat(agent): real provider-driven tool-calling loop guarded by LoopGuard"
```

---

### Task 8: Offline deterministic demo (real tool, no LLM)

**Files:**
- Modify: `src/loopguard/demos.py`
- Test: `tests/test_demos.py` (extend existing)

**Interfaces:**
- Consumes: `read_file` (Task 4), `LoopGuard` (Task 6).
- Produces: `run_offline_demo(action: str = "pause", root: str | None = None) -> None` (replaces the synthetic `run_broken_agent` filesystem). Keep `run_broken_agent` as a thin alias calling `run_offline_demo` for backward-compat with existing `tests/test_demos.py`, OR update the existing tests. Chosen: replace the fake `demo_read_file` with the real `read_file`, keep function name `run_broken_agent(use_guard=True, action="pause", root=None)`.

**Behavior:** A real, deterministic retry loop. It calls the **real** `read_file` against a list of paths that genuinely do not exist under `root` (default: a fresh temp dir created with `tempfile.mkdtemp()` so it is real but empty). Real failures → real events → real Layer-1 trip. Honest `cost_usd=0.0`, `tokens=0` (no LLM). On `inject`, it reads a file that genuinely DOES exist (a real file the demo writes into the temp dir, e.g. `notes.txt`) to show real recovery.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_demos.py
def test_offline_demo_uses_real_fs_and_zero_cost(tmp_path, monkeypatch, capsys):
    import loopguard.demos as demos

    # force a confirmed loop with no judge by using warn->no, use a fake pause that terminates
    monkeypatch.setattr("loopguard.guard.pause_for_action", lambda d: _term(d))
    monkeypatch.chdir(tmp_path)
    demos.run_broken_agent(use_guard=True, action="pause", root=str(tmp_path))
    out = capsys.readouterr().out
    assert "$0.00" in out or "cost: $0.000" in out  # honest zero cost


def _term(d):
    d.developer_action = "terminate"
    d.allowed = False
    return d
```

(Keep the existing `test_correction_injection_recovers` and `test_no_guard_runs_to_exhaustion`; update them only if the signature change requires the new `root` kwarg — they call `run_broken_agent(use_guard=True)` which still works because `root` defaults to a temp dir.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_demos.py -v`
Expected: FAIL (new test fails / `root` kwarg missing).

- [ ] **Step 3: Write minimal implementation**

Rewrite `src/loopguard/demos.py`:
```python
from __future__ import annotations

import tempfile
from pathlib import Path

from .config import LoopGuardConfig
from .event import LoopEvent
from .guard import LoopGuard
from .tools import read_file

# Real but empty workspace: these genuinely do not exist there.
MISSING_PATHS = ["package.json", "./package.json", "app/package.json"]


def run_broken_agent(use_guard: bool = True, action: str = "pause", root: str | None = None) -> None:
    workspace = Path(root) if root else Path(tempfile.mkdtemp(prefix="loopguard-demo-"))
    # A real file that genuinely exists, used to demonstrate real recovery.
    recovery = workspace / "notes.txt"
    recovery.write_text("This is the file you were actually looking for.")

    guard = LoopGuard(LoopGuardConfig(action=action, max_tool_calls=30)) if use_guard else None
    correction = None
    try:
        for i in range(10):
            path = "notes.txt" if correction else MISSING_PATHS[i % len(MISSING_PATHS)]
            result = read_file(path, root=workspace)  # REAL disk read
            is_err = result.startswith("Error:")
            print(f'[{i + 1}] read_file("{path}") -> {"not found" if is_err else result[:40]} '
                  f'cost: $0.000')  # honest: no LLM, zero cost
            if not is_err:
                return
            if guard is None:
                continue
            decision = guard.observe(
                LoopEvent(
                    run_id="demo",
                    agent="repo-agent",
                    kind="tool_call",
                    tool_name="read_file",
                    tool_args={"path": path},
                    error=result,
                    tokens=0,
                    cost_usd=0.0,
                )
            )
            if decision.developer_action == "inject" and decision.suggested_message:
                correction = decision.suggested_message
            elif decision.developer_action == "inject":
                correction = "Read notes.txt instead."
            elif decision.tripped and not decision.allowed:
                return
        print("[10] still trying package.json cost: $0.000")
    finally:
        if guard is not None:
            guard.export_jsonl("runs/last.jsonl")


def run_pingpong_demo(action: str = "pause") -> None:
    guard = LoopGuard(LoopGuardConfig(action=action, enable_semantic=False, enable_budget=False))
    print(
        "Ping-pong demo: two agents pass the same failure back and forth "
        "(A-B-A-B). Watch LoopGuard detect the oscillation."
    )
    messages = [
        ("agent-a", "tool failed: package.json not found"),
        ("agent-b", "retry with same approach"),
    ]
    try:
        for i in range(4):
            agent, msg = messages[i % 2]
            print(f"{agent}: {msg}")
            decision = guard.observe(
                LoopEvent(run_id="pingpong", agent=agent, kind="agent_message", output_text=msg)
            )
            if decision.tripped:
                if decision.allowed:
                    continue
                print("LoopGuard terminated the ping-pong loop.")
                break
        s = guard.summary()
        print(f"Total: {s['events']} events · {s['tokens']} tokens · ${s['cost_usd']:.4f}")
    finally:
        guard.export_jsonl("runs/last.jsonl")
```

Note: the existing `test_correction_injection_recovers` monkeypatches `loopguard.guard.pause_for_action` to set `developer_action="inject"` + `suggested_message="read pyproject.toml instead"`. With the rewrite, an `inject` with a `suggested_message` sets `correction = suggested_message`, but the real recovery file is `notes.txt`. Update that existing test's assertion to check recovery via `notes.txt` instead of `pyproject.toml`: assert the output contains `notes.txt` and `This is the file you were actually looking for.` (Update the fake `suggested_message` to `"read notes.txt"` so the corrected path is taken — but the demo forces `path="notes.txt"` whenever `correction` is truthy regardless of its text, so the existing fake message is fine; just change the assertions to look for `notes.txt`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_demos.py -v`
Expected: PASS (update assertions as noted; all demo tests green).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/demos.py loopguard/tests/test_demos.py && git commit -m "feat(demos): offline demo uses the real read_file tool, honest $0 cost"
```

---

### Task 9: Live demo (`live_demo.py`) + CLI wiring

**Files:**
- Create: `src/loopguard/live_demo.py`
- Modify: `src/loopguard/cli.py`, `src/loopguard/__init__.py`
- Delete: `src/loopguard/cerebras_demo.py`
- Test: `tests/test_live_demo.py` (create), `tests/test_cli_smoke.py` (create)

**Interfaces:**
- Consumes: `make_provider` (Task 2), `LLMJudge` (Task 3), `run_agent` (Task 7), `LoopGuard` (Task 6), `READ_FILE_SCHEMA`/`read_file` (Task 4).
- Produces: `run_live(model: str = "cerebras/gpt-oss-120b", provider: str = "auto", mode: str = "pause", root: str | None = None, use_guard: bool = True) -> None`.

**Behavior:** Build provider; build judge from the same provider (only if `use_guard` and key available); build `LoopGuard(config(action=mode), judge=judge)`. Run `run_agent` against the LoopGuard repo dir (`root` default = the repo root, which has `pyproject.toml` but no `package.json`). System prompt frames it as an npm project so the agent genuinely loops; the judge genuinely discovers `pyproject.toml`. Export to `runs/last.jsonl`. Print real token/cost totals from `guard.summary()`. If no API key/provider error, print a friendly message and return (no crash).

- [ ] **Step 1: Write the failing test** (mocked provider; no network)

```python
# tests/test_live_demo.py
from loopguard import live_demo
from loopguard.providers.base import LLMResult


class _TC:
    def __init__(self, i):
        self.id = f"c{i}"
        self.type = "function"
        self.function = type("F", (), {"name": "read_file", "arguments": '{"path": "package.json"}'})()


class _FakeProvider:
    model = "fake/model"

    def __init__(self):
        self.n = 0

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512):
        self.n += 1
        # After a correction is injected as a user msg, stop looping with a final answer.
        if any(m.get("role") == "user" and "notes" in str(m.get("content", "")).lower()
               or m.get("role") == "user" and "pyproject" in str(m.get("content", "")).lower()
               for m in messages[2:]):
            return LLMResult(text="Found it.", prompt_tokens=10, completion_tokens=2, total_tokens=12)
        return LLMResult(text="", tool_calls=[_TC(self.n)], prompt_tokens=100,
                         completion_tokens=10, total_tokens=110, cost_usd=0.001)


def test_run_live_with_mocked_provider_and_judge(monkeypatch, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
    fake = _FakeProvider()
    monkeypatch.setattr(live_demo, "make_provider", lambda model, provider: fake)

    # judge confirms the loop and suggests reading pyproject.toml
    from loopguard.judge import JudgeVerdict

    class _Judge:
        def judge(self, events, task=None, detector=None):
            return JudgeVerdict(is_loop=True, reasoning="stuck on package.json",
                                suggested_correction="This is a Python project; read pyproject.toml",
                                cost_usd=0.0005)

    monkeypatch.setattr(live_demo, "LLMJudge", lambda provider: _Judge())
    monkeypatch.chdir(tmp_path)
    live_demo.run_live(model="fake/model", mode="auto", root=str(tmp_path))
    out = capsys.readouterr().out
    assert "read_file" in out
    assert "pyproject.toml" in out  # the real correction was injected
```

```python
# tests/test_cli_smoke.py
from typer.testing import CliRunner

from loopguard.cli import app

runner = CliRunner()


def test_inspect_empty(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    result = runner.invoke(app, ["inspect", str(p)])
    assert result.exit_code == 0
    assert "No events" in result.stdout


def test_demo_offline_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # pause would block on input; use the non-interactive 'warn'-like path by terminating
    monkeypatch.setattr("loopguard.guard.pause_for_action",
                        lambda d: d.copy(update={"developer_action": "terminate", "allowed": False}))
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_live_demo.py tests/test_cli_smoke.py -v`
Expected: FAIL (`loopguard.live_demo` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# src/loopguard/live_demo.py
from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .agent import run_agent
from .config import LoopGuardConfig
from .guard import LoopGuard
from .judge import LLMJudge
from .providers import make_provider
from .tools import READ_FILE_SCHEMA, read_file

console = Console()

_SYSTEM = (
    "You are a JavaScript repo inspector. This is an npm project. Find and read the "
    "package.json file using the read_file tool. The file must be named package.json — "
    "keep trying different directory paths until you find it."
)
_TASK = "Find package.json for this npm project."


def _repo_root() -> Path:
    # src/loopguard/live_demo.py -> repo root is three parents up.
    return Path(__file__).resolve().parents[2]


def run_live(
    model: str = "cerebras/gpt-oss-120b",
    provider: str = "auto",
    mode: str = "pause",
    root: str | None = None,
    use_guard: bool = True,
) -> None:
    try:
        prov = make_provider(model, provider)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    workspace = Path(root) if root else _repo_root()
    console.rule(f"[bold cyan]LoopGuard × {getattr(prov, 'model', model)}")

    guard = None
    if use_guard:
        judge = LLMJudge(prov)
        guard = LoopGuard(LoopGuardConfig(action=mode, max_tool_calls=15), judge=judge)
        console.print("[dim]Agent will hunt for package.json (a Python repo). Watch it loop, "
                      "then watch the judge route it to the real file.[/dim]\n")
    else:
        console.print("[bold yellow]UNGUARDED run:[/bold yellow] no LoopGuard — burns tokens.\n")

    def on_event(step, name, args, output):
        is_err = output.startswith("Error:")
        path = args.get("path", "?")
        color = "red" if is_err else "green"
        console.print(f"[{color}][{step}] {name}(\"{path}\") -> {output[:70]}[/{color}]")

    try:
        result = run_agent(
            prov,
            system=_SYSTEM,
            task=_TASK,
            tools_schema=[READ_FILE_SCHEMA],
            tool_impls={"read_file": lambda path: read_file(path, root=workspace)},
            guard=guard,
            run_id="live-demo",
            agent_name="repo-inspector",
            max_steps=15,
            on_event=on_event,
        )
        if result.final_text:
            console.print(f"\n[bold green]Agent:[/bold green] {result.final_text}")
        if result.stopped_by_guard:
            console.print("[bold red]LoopGuard stopped the agent.[/bold red]")
    except Exception as exc:  # noqa: BLE001 - real API/runtime errors shown, not crashed
        console.print(f"[red]Run error: {exc}[/red]")
    finally:
        if guard is not None:
            s = guard.summary()
            console.print(
                f"\n[dim]Total: {s['events']} events · {s['tokens']} tokens · "
                f"${s['cost_usd']:.4f} (+ judge ${s['judge_cost_usd']:.4f})[/dim]"
            )
            guard.export_jsonl("runs/last.jsonl")
            console.print("[dim]Saved run to runs/last.jsonl[/dim]")
```

Rewrite `src/loopguard/cli.py` `demo` command:
```python
@app.command()
def demo(
    live: bool = typer.Option(False, "--live", help="Run a real LLM agent (needs an API key)."),
    model: str = typer.Option("cerebras/gpt-oss-120b", help="Model id (litellm routing string)."),
    provider: str = typer.Option("auto", help="auto | litellm | cerebras"),
    mode: DemoMode = typer.Option(DemoMode.pause, help="pause | flag | auto | warn"),
    scenario: DemoScenario = DemoScenario.single,
    guard: bool = typer.Option(True, "--guard/--no-guard"),
):
    if scenario == DemoScenario.pingpong:
        from .demos import run_pingpong_demo

        run_pingpong_demo(action=mode.value)
        return
    if live or scenario == DemoScenario.cerebras:
        from .live_demo import run_live

        run_live(model=model, provider=provider, mode=mode.value, use_guard=guard)
        return
    from .demos import run_broken_agent

    run_broken_agent(use_guard=guard, action=mode.value)
```
Update the `DemoMode` enum to:
```python
class DemoMode(str, Enum):
    pause = "pause"
    flag = "flag"
    auto = "auto"
    warn = "warn"
```
(Remove the old `guard`/`no-guard` `DemoMode`; `--guard/--no-guard` is now a boolean option.)

Update `src/loopguard/__init__.py` to export new symbols (append to `__all__` and imports):
```python
from .agent import AgentResult, run_agent
from .judge import JudgeVerdict, LLMJudge
from .providers import make_provider
```
(Keep existing exports; add these names to `__all__`.)

Delete `src/loopguard/cerebras_demo.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pip install -e ".[dev]" -q && python3 -m pytest tests/test_live_demo.py tests/test_cli_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add -A loopguard/src/loopguard loopguard/tests/test_live_demo.py loopguard/tests/test_cli_smoke.py && git commit -m "feat(live): provider-agnostic live agent demo + CLI (--live/--model/--provider/--mode)"
```

---

### Task 10: Real LiteLLM integration callback

**Files:**
- Modify: `src/loopguard/integrations/litellm_callback.py`
- Test: `tests/test_litellm_callback.py`

**Interfaces:**
- Consumes: `LoopGuard` (Task 6), `cost_for` (Task 1).
- Produces: `LoopGuardLiteLLMCallback(guard, run_id="litellm", agent="litellm")` — works as a litellm `CustomLogger` (duck-typed if litellm absent), capturing real tokens + real cost into `guard.observe()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_litellm_callback.py
from loopguard import LoopGuard, LoopGuardConfig
from loopguard.integrations.litellm_callback import LoopGuardLiteLLMCallback


class _Usage:
    prompt_tokens = 200
    completion_tokens = 50
    total_tokens = 250


class _Resp:
    model = "gpt-4o"
    usage = _Usage()
    choices = [type("C", (), {"message": type("M", (), {"content": "hello"})()})()]


def test_callback_records_real_tokens_and_cost():
    guard = LoopGuard(LoopGuardConfig(action="warn", enable_semantic=False, enable_budget=False))
    cb = LoopGuardLiteLLMCallback(guard)
    cb.log_success_event(
        kwargs={"model": "gpt-4o"}, response_obj=_Resp(), start_time=0, end_time=1
    )
    s = guard.summary()
    assert s["events"] == 1
    assert s["tokens"] == 250
    assert s["cost_usd"] > 0.0  # gpt-4o priced in the table
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_litellm_callback.py -v`
Expected: FAIL (current callback records no tokens/cost; `s["tokens"] == 0`).

- [ ] **Step 3: Write minimal implementation**

Replace `src/loopguard/integrations/litellm_callback.py`:
```python
from __future__ import annotations

from typing import Any

from loopguard.event import LoopEvent
from loopguard.guard import LoopGuard
from loopguard.pricing import cost_for

try:  # subclass the real base when litellm is installed; duck-type otherwise
    from litellm.integrations.custom_logger import CustomLogger as _Base
except Exception:  # noqa: BLE001
    class _Base:  # minimal stand-in
        pass


def _usage(response_obj: Any) -> tuple[int, int, int]:
    usage = getattr(response_obj, "usage", None) or {}
    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
    pt = int(get("prompt_tokens") or 0)
    ct = int(get("completion_tokens") or 0)
    tt = int(get("total_tokens") or (pt + ct))
    return pt, ct, tt


def _model(kwargs: dict, response_obj: Any) -> str:
    return (kwargs or {}).get("model") or getattr(response_obj, "model", "") or "unknown"


def _cost(kwargs: dict, response_obj: Any, pt: int, ct: int) -> float:
    try:
        import litellm

        c = litellm.completion_cost(completion_response=response_obj)
        if c:
            return float(c)
    except Exception:  # noqa: BLE001
        pass
    return cost_for(_model(kwargs, response_obj), pt, ct)


class LoopGuardLiteLLMCallback(_Base):
    def __init__(self, guard: LoopGuard, run_id: str = "litellm", agent: str = "litellm"):
        super().__init__()
        self.guard = guard
        self.run_id = run_id
        self.agent = agent

    def log_success_event(self, kwargs, response_obj, start_time=None, end_time=None):
        pt, ct, tt = _usage(response_obj)
        self.guard.observe(
            LoopEvent(
                run_id=self.run_id,
                agent=self.agent,
                kind="llm_call",
                input_text=str((kwargs or {}).get("messages", "")),
                output_text=str(response_obj),
                tokens=tt,
                cost_usd=_cost(kwargs, response_obj, pt, ct),
                metadata={"model": _model(kwargs, response_obj)},
            )
        )

    def log_failure_event(self, kwargs, response_obj, start_time=None, end_time=None):
        self.guard.observe(
            LoopEvent(
                run_id=self.run_id,
                agent=self.agent,
                kind="error",
                error=str(response_obj),
                metadata={"model": _model(kwargs, response_obj)},
            )
        )

    async def async_log_success_event(self, kwargs, response_obj, start_time=None, end_time=None):
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(self, kwargs, response_obj, start_time=None, end_time=None):
        self.log_failure_event(kwargs, response_obj, start_time, end_time)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/ayanbinsaif/agent/loopguard && python3 -m pytest tests/test_litellm_callback.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add loopguard/src/loopguard/integrations/litellm_callback.py loopguard/tests/test_litellm_callback.py && git commit -m "feat(integrations): real LiteLLM CustomLogger with token+cost capture"
```

---

### Task 11: Packaging, docs, full verification

**Files:**
- Modify: `pyproject.toml`, `README.md`
- Create: `examples/litellm_guarded_agent.py`

**Interfaces:** none (final wiring + docs).

- [ ] **Step 1: Add the litellm extra to `pyproject.toml`**

Change the optional-dependencies block to add:
```toml
litellm = ["litellm>=1.40"]
```
(Keep `dev`, `cerebras`.)

- [ ] **Step 2: Write a runnable example**

```python
# examples/litellm_guarded_agent.py
"""Guard ANY litellm-based agent by registering one callback.

Run: OPENAI_API_KEY=... python examples/litellm_guarded_agent.py
"""
import litellm

from loopguard import LoopGuard, LoopGuardConfig
from loopguard.integrations.litellm_callback import LoopGuardLiteLLMCallback

guard = LoopGuard(LoopGuardConfig(action="flag"))
litellm.callbacks = [LoopGuardLiteLLMCallback(guard)]

for _ in range(5):
    litellm.completion(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "Say the same thing every time."}],
    )

print(guard.summary())
```

- [ ] **Step 3: Update `README.md`** — replace the "How semantic detection works" / "Cerebras setup" / "CLI demos" sections so they describe: two layers (deterministic + LLM judge), provider-agnostic (`--model`, `--provider`), action modes (`pause`/`flag`/`auto`), offline mode (no key, honest $0), and the LiteLLM callback as the "guard any agent" path. Update the CLI block to:
```bash
loopguard demo                                   # offline, no key, real failing tool, $0
loopguard demo --live                            # real agent (Cerebras default)
loopguard demo --live --model openai/gpt-4o      # any provider
loopguard demo --live --mode auto                # auto-repair
loopguard demo --live --mode flag                # detect + report, non-blocking
loopguard inspect runs/last.jsonl
```
Remove any remaining claim implying costs are illustrative — they are now real (or honest $0 offline).

- [ ] **Step 4: Reinstall and run the FULL suite**

Run:
```bash
cd /Users/ayanbinsaif/agent/loopguard && python3 -m pip install -e ".[dev]" -q && python3 -m pytest tests/ -q
```
Expected: PASS (all tests green — existing 13 + new pricing/providers/judge/tools/config/action/agent/live/cli/litellm tests).

- [ ] **Step 5: Manual live smoke (real Cerebras, requires key)**

Run:
```bash
cd /Users/ayanbinsaif/agent/loopguard && set -a && . ./.env && set +a && printf 't\n' | loopguard demo --live --model cerebras/gpt-oss-120b
cd /Users/ayanbinsaif/agent/loopguard && loopguard demo --live --no-guard --model cerebras/gpt-oss-120b
cd /Users/ayanbinsaif/agent/loopguard && loopguard inspect runs/last.jsonl
```
Expected: guarded run loops on real files, judge suggests a real correction (reading pyproject.toml), summary shows real tokens + real cost + judge cost; no-guard run burns more; inspect renders the Rich table. (If the installed litellm lacks a Cerebras route, fall back to `--provider cerebras`.)

- [ ] **Step 6: Commit**

```bash
cd /Users/ayanbinsaif/agent && git add -A loopguard && git commit -m "build: litellm extra, runnable example, honest README, full green suite"
```

---

## Self-Review

**Spec coverage:**
- §2 two layers → Tasks 3 (judge) + 6 (guard integration). ✓
- §3 provider abstraction → Task 2. ✓
- §4 real cost → Task 1; wired in Tasks 2, 7, 10. ✓
- §5 real tools → Task 4; used in Tasks 7, 8, 9. ✓
- §6 action modes (pause/flag/auto) → Tasks 5 (config) + 6 (guard/ui). ✓
- §7 offline mode → Task 8. ✓
- §8 real LiteLLM integration → Task 10 + example in Task 11. ✓
- §9 CLI surface → Task 9. ✓
- §10 testing → every task is TDD; key-free tests run in CI. ✓
- §11 dependencies (litellm extra) → Task 11. ✓
- §12/§13 out-of-scope/future → not built (no tasks), as intended. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code. ✓

**Type consistency:** `LLMResult`, `LLMProvider`, `make_provider`, `JudgeVerdict`, `LLMJudge.judge`, `read_file`, `run_agent`/`AgentResult`, `LoopGuard(config, judge)`, `observe(event, task=None)`, `summary()[...,"judge_cost_usd"]`, decision fields `judged/judge_reasoning/judge_confidence` — names/signatures are consistent across tasks. ✓

**Note for executor:** Task 8 changes the recovery file from `pyproject.toml` to `notes.txt` in the offline demo; update the pre-existing `test_correction_injection_recovers` assertions accordingly (look for `notes.txt`). Task 9 deletes `cerebras_demo.py`; the old `tests/` had no direct test of it, but the CLI `DemoScenario.cerebras` path now routes to `run_live`, preserved for backward compat.
