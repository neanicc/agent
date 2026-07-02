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
- Keep the existing MVP demos local, deterministic, and independently demoable.
- The approved production roadmap in `../docs/superpowers/` authorizes the local daemon, optional
  hosted control plane, native iOS client, web console, and Codex/Claude integrations. Keep those
  product layers outside the small detector core and behind explicit optional dependencies.
- The detector core must remain provider-independent. Optional provider integrations may document
  their own credentials, but imports stay lazy and no provider is required to import or test the
  core package.
- Do not require any external API key for core tests or demos.
- Cerebras must remain optional and lazy-imported.
- Preserve deterministic demo behavior.
- New production tests use fakes, recorded fixtures, or local test services by default. Live
  provider, cloud, notification, GitHub, and App Store checks are separate opt-in suites.
