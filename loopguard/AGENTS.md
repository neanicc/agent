# LoopGuard agent notes

## Layout
- `src/loopguard/`: package code.
- `src/loopguard/detectors/`: exact, semantic, ping-pong, and budget detectors.
- `src/loopguard/integrations/`: optional integrations; imports must remain lazy.
- `examples/`: deterministic local demos plus optional Cerebras/LiteLLM examples.
- `tests/`: pytest suite.

## Commands
- Install dev mode: `pip install -e ".[dev]"`
- Run tests: `pytest`
- Lint/format: `ruff check .` and `ruff format .`
- Local demo: `loopguard demo` or `python examples/broken_agent.py`

## Rules
- Keep the MVP local and demoable.
- Avoid SaaS/dashboard scope.
- Do not require OpenAI or add OpenAI API key references.
- Do not require any external API key for core tests or demos.
- Cerebras must remain optional and lazy-imported.
- Preserve deterministic demo behavior.
