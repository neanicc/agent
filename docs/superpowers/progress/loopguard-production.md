# LoopGuard Production Implementation Progress

- Branch: `feat/loopguard-production`
- Current milestone/tranche: 2 of 13 — attached Codex and Claude integrations
- Current plan and immutable task ID: `docs/superpowers/plans/2026-07-02-agent-integrations.md`, `INTEGRATIONS-T03`
- Last completed step: `INTEGRATIONS-T02` completed inline with a 100 ms framed local hook client, strict untrusted-input normalization, locally derived host/repository/worktree/session identities, pre-transport redaction, bounded output, typed approval requests, explicit fail-open/fail-closed rendering, honest Codex coverage warnings, and daemon restart replay. The canonical hook contract now distinguishes `turn.completed` from `session.stopped` so vendor `Stop` events cannot evict detector state.
- Expected/observed last commit: Expected task commit subject `feat: normalize native agent hooks`; observed predecessor `a448145 feat: define agent adapter capabilities`.
- Task dependencies and owned files: `INTEGRATIONS-T03` depends on the T02 hook entry/client plus current Codex plugin and hook schemas; it owns the Codex plugin package, safe locked fallback installer, integration CLI commands, compatibility evidence, and focused installer tests.
- Red/green/regression commands with exit codes and counts: T02 RED exited 2 during collection because the hook modules were absent; expanded focused T02 GREEN exited 0 with 103 passed, including real framed hook-to-daemon/restart/cross-turn detection and p95 under 20 ms; full Ruff and diff checks exited 0; `python -m pytest -q -W error::RuntimeWarning` exited 0 with 376 passed.
- Evidence/artifact paths: `loopguard/src/loopguard/adapters/hook_client.py`, `loopguard/src/loopguard/adapters/normalize_hook.py`, `loopguard/src/loopguard/adapters/hook_entry.py`, `loopguard/tests/adapters/test_hook_entry.py`, corrected event-contract tests, current official Codex/Claude hook references, and installed CLI evidence (`codex-cli 0.144.0`, Claude Code `2.1.210`).
- Last successful cumulative verification: 2026-07-15 — full warning-strict Python suite 376 passed after native hook normalization, full Ruff clean, diff whitespace clean, with the single recorded external Starlette deprecation only.
- Known baseline failures: No LoopGuard-owned baseline failures remain. One external `StarletteDeprecationWarning` from FastAPI/TestClient is recorded and is not suppressed.
- Active blockers: None.
- Next exact action: Commit `INTEGRATIONS-T02` as `feat: normalize native agent hooks`, verify a clean tree and commit subject, then begin `INTEGRATIONS-T03` with Codex plugin/fallback idempotency RED tests against current hook schema behavior.
