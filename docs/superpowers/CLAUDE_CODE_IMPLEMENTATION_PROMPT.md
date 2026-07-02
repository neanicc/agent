# Claude Code Full Implementation Prompt

Copy everything inside the following block into Claude Code from the repository root.

```text
You are implementing the complete LoopGuard production roadmap from top to bottom.

The finished product is a local-first reliability control plane for Codex and Claude. It must:

- Attach automatically to supported Codex and Claude sessions.
- Support attached and managed local sessions plus supported cloud-session hooks.
- Detect loops with deterministic zero-token monitoring.
- Detect file changes from any agent immediately, journal them, warn about collisions, and give
  every managed agent bounded current context without transferring hidden chain-of-thought.
- Support structured Codex-to-Claude and Claude-to-Codex handoff without replaying full transcripts.
- Prevent newly introduced regressions through baseline and evidence-based verification.
- Select model and reasoning effort at managed phase boundaries within explicit cost budgets.
- Learn editable UI/product preferences without silently turning subjective preferences into hard rules.
- Accelerate Playwright iteration while retaining complete final verification.
- Let authenticated users monitor and control supported managed sessions from phone/web through
  safe, expiring, replay-protected actions.
- Reproduce supported pipeline failures and publish only verified draft repair PRs.
- Prove with observed usage and holdouts that LoopGuard's own token/cost overhead is lower than the
  paid work it prevents; never report estimated savings as observed savings.
- Ship a native iOS 26 app and web dashboard with deliberate hierarchy, selective Liquid Glass,
  accessibility, responsive layouts, and visual-regression gates rather than generic AI styling.
- Meet the production security, privacy, reliability, recovery, and release gates in the plans.

This is an execution request, not a design exercise. Continue sequentially until every plan is
implemented and verified. Do not stop merely because one plan or phase is complete.

GIT SAFETY AND SETUP

1. Confirm this is the intended repository:
   git rev-parse --show-toplevel
   git remote -v

2. Inspect the working tree:
   git status --short

3. If there are uncommitted changes, conflicts, or untracked files you did not create, STOP and
   ask the user. Do not stash, reset, clean, delete, overwrite, or rebase existing work.
   Exception: if the only untracked file is root `AGENTS.md` and the user confirms it was generated
   by their gstack/Codex setup, read and preserve it, never stage it, and continue. Any other dirty
   state still requires confirmation.

4. Confirm the remote and main branch exist, then update safely:
   git fetch origin
   git switch main
   git pull --ff-only origin main

5. Confirm these files exist after pulling:
   test -f docs/superpowers/specs/2026-07-02-loopguard-reliability-control-plane-design.md
   test -f docs/superpowers/plans/2026-07-02-loopguard-productization-master.md
   test -f docs/superpowers/CLAUDE_CODE_IMPLEMENTATION_PROMPT.md

6. Never implement or push directly on main. Use the single integration branch
   `feat/loopguard-production`.

   - If it does not exist locally or remotely:
     git switch -c feat/loopguard-production
   - If it already exists locally, switch to it only after confirming it is the intended
     implementation branch.
   - If only `origin/feat/loopguard-production` exists:
     git switch --track origin/feat/loopguard-production
   - Never force-push this branch.

7. The user authorizes scoped implementation commits, pushing this feature branch, and creating or
   updating one draft PR. The user does not authorize merging to main, production deployment,
   destructive Git operations, automatic repair publication against a real repository, or use of
   production credentials.

REQUIRED READING

Before changing code, read these completely:

- README.md
- Root `AGENTS.md` if present, whether tracked or user-owned
- loopguard/AGENTS.md
- docs/superpowers/specs/2026-07-02-loopguard-reliability-control-plane-design.md
- docs/superpowers/plans/2026-07-02-loopguard-productization-master.md
- Every existing 2026-07-02 subsystem plan listed below
- loopguard/src/loopguard/event.py
- loopguard/src/loopguard/decision.py
- loopguard/src/loopguard/guard.py
- loopguard/src/loopguard/config.py
- loopguard/src/loopguard/server/app.py
- loopguard/src/loopguard/server/runs.py
- loopguard/pyproject.toml
- cloud-app/package.json
- Existing tests under loopguard/tests and cloud-app/src/__tests__

Treat the post-productization developer journey as a release contract:

- `loopguard quickstart` reaches a real offline daemon/guard result with no key in under two
  minutes from an installed package.
- `loopguard setup --agent auto` plus explicit vendor trust reaches a protected existing session
  in under five minutes.
- Every CLI/API error gives a stable code, problem, cause, fix, and docs reference.
- Setup, doctor, uninstall, config, update, migration, dry-run, JSON, and redacted diagnostic paths
  are documented and tested.
- Root/package READMEs describe the production product first; hackathon live-provider demos are
  optional and clearly labelled.

Before implementing provider integrations, re-check the current official interfaces:

- https://developers.openai.com/codex/hooks
- https://developers.openai.com/codex/app-server
- https://developers.openai.com/codex/remote-connections
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/agent-sdk/typescript
- https://code.claude.com/docs/en/claude-code-on-the-web

Generate compatibility fixtures from installed CLIs/SDKs. If current official schemas differ from
the plan, preserve the product invariant, update the compatibility plan and tests first, and do not
invent an unsupported capability. In particular, repo-local Codex cloud hook execution remains
conditional until a real compatibility smoke test proves it.

Understand these boundaries before implementation:

- Preserve the stable `LoopEvent -> LoopGuard -> LoopDecision` circuit breaker.
- `ControlEvent`, `PolicyDecision`, context, verification, routing, repair, and remote actions are
  outer product-layer contracts.
- Freeze canonical names before dependent work: `local_log_seq`, `repo_seq`, `session_seq`,
  `cloud_ingest_seq`, and `client_stream_seq` are different domains; `ActionTarget` supports
  session/repair/host/verification/repository; policy actions are allow, warn, pause, interrupt,
  inject, and request approval.
- Every ingested event must follow the real composition path: validate/redact -> durable store ->
  dispatcher -> existing per-session guard and registered subsystem handlers -> normalized
  decision acknowledgement. Persistence without dispatch is not a completed foundation.
- The current FastAPI server and Expo application are prototypes. Do not expand them into the
  production architecture unless a plan explicitly requires migration compatibility.
- Monitoring, routing features, change detection, test selection, and ordinary verification must
  not invoke a model.
- Managed Claude uses user-supplied API/provider credentials. Do not offer or proxy `claude.ai`
  login, subscription rate limits, or session credentials unless Anthropic has separately granted
  explicit approval. Attached Claude hooks do not require managed-mode API credentials.
- Do not read, store, request, summarize, or transfer hidden chain-of-thought.
- Concurrent managed agents must not share one writable worktree.
- Attached vendor sessions cannot be transparently moved; detect shared-primary-worktree risk and
  apply the configured warning/block without claiming managed isolation.
- “Verified” requires a proof contract and clean baseline recorded before the first mutating tool.
  Late-attached or unbaselined passing checks are explicitly inconclusive.
- Auto-Heal must never merge or deploy a generated repair in this implementation.
- The reference hosted deployment is AWS (EKS, RDS PostgreSQL, S3, KMS, ECR, Route 53, and GitHub
  OIDC). Keep deployment regions/account IDs configurable and never apply infrastructure without
  separate user approval.

BASELINE

Before Task 1, install only dependency groups that exist on main and run the current baseline:

cd loopguard
python -m pip install -e ".[dev,server,litellm]"
python -m pytest -q
ruff check src tests
cd ../cloud-app
npm ci
npm test
npx tsc --noEmit
cd ..

Record exact commands, exit codes, passing/failing counts, and environmental failures. A missing
optional external credential is not a reason to run a live provider test. Use the fakes specified
by the plans.

The foundation plan adds an `all-dev` extra and later plans add/update product-specific extras.
After every `pyproject.toml` optional-dependency change, reinstall the changed package before
running that task's tests. Once `all-dev` exists, use:

cd loopguard
python -m pip install -e ".[all-dev]"

For every Node package (`claude-bridge`, `browser-broker`, `playwright-loopguard`, and `apps/web`),
the task must create and commit `package-lock.json`, then use `npm ci`. Never let `npm test` pass
against an unrecorded local `node_modules` state.

RESUMABLE PROGRESS

Create `docs/superpowers/progress/loopguard-production.md` before implementation with:

# LoopGuard Production Implementation Progress

- Branch:
- Current plan:
- Current task:
- Last completed step:
- Expected/observed last commit:
- Last successful verification:
- Known baseline failures:
- Active blockers:
- Next exact action:

After every task and before context compaction or session exit, update this file. On every resumed
session, read it, compare its expected commit subject with `git log -1 --oneline`, verify the
working tree, rerun the last relevant focused test, then continue from `Next exact action`. Before
each task commit, update the progress file with the task result, next exact action, and planned
commit subject, and stage the progress file with the task's files. The `git add` snippets in plans
are path guidance; always include `docs/superpowers/progress/loopguard-production.md`. Leave a clean
tree after the commit rather than creating a second commit solely to write its hash. Do not redo
completed tasks unless verification shows they regressed.

EXECUTION ORDER

Execute every plan in this exact order:

1. `docs/superpowers/plans/2026-07-02-control-plane-foundation.md`
   Complete Tasks 1–6.

2. `docs/superpowers/plans/2026-07-02-agent-integrations.md`
   Complete Tasks 1–8.

3. `docs/superpowers/plans/2026-07-02-context-coordination.md`
   Complete Tasks 1–7.

4. `docs/superpowers/plans/2026-07-02-regression-verification.md`
   Complete Tasks 1–7.

5. `docs/superpowers/plans/2026-07-02-model-routing-cost-control.md`
   Complete Tasks 1–7.

6. `docs/superpowers/plans/2026-07-02-preference-engine.md`
   Complete Tasks 1–6.

7. `docs/superpowers/plans/2026-07-02-browser-acceleration.md`
   Complete Tasks 1–7.

8. `docs/superpowers/plans/2026-07-02-cloud-control-plane.md`
   Complete Tasks 1–9.

9. `docs/superpowers/plans/2026-07-02-auto-heal-pipelines.md`
   Complete Tasks 1–9.

10. `docs/superpowers/plans/2026-07-02-control-surfaces.md`
    Complete Tasks 1–11. Auto-Heal precedes this plan because the generated client contract requires
    implemented `/v1/repairs` endpoints.

11. `docs/superpowers/plans/2026-07-02-production-hardening.md`
    Complete Tasks 1–10.

Do not reorder these plans and do not implement a later plan early merely because its files look
convenient. If a required API from a later plan is discovered, treat that as a dependency defect:
explain it and fix the plan/order before continuing.

This single branch/PR is an explicit user decision. Keep it reviewable:

- One scoped commit per completed numbered task; no mixed-plan commits.
- One milestone tag in the progress file per completed plan, including cumulative test evidence.
- Update the draft PR body after each plan with a collapsible task/commit/test section.
- Before starting the next plan, verify every earlier plan's cumulative gate, schema/API contract
  compatibility, migration head, and clean working tree.
- If recovery requires reverting a broken task, use a normal revert commit after user approval;
  never rewrite or force-push branch history.
- Essential security preconditions live in their owning early plans. The final hardening plan
  audits and exercises them; it is not permission to defer local permissions, peer identity,
  repository trust, RLS, action proof, encryption, or sandbox isolation until the end.

TASK EXECUTION RULES

Use `superpowers:executing-plans` if installed. For implementation and bug fixes, use the
test-driven-development workflow. Use systematic debugging for unexpected failures. Use
verification-before-completion before every completion claim.

For every task:

1. Read the entire task before editing.
2. Confirm its dependencies and referenced types already exist.
3. Write the specified failing test.
   Implement every illustrative helper/fixture referenced by the snippet in that test package's
   `conftest.py` or explicit support module; never commit pseudocode-only names such as
   `make_git_repo`, `fake_cloud`, `profile`, or `event_fixture`.
4. Run the exact focused test and confirm it fails for the expected reason.
5. If it passes before implementation, investigate whether behavior already exists or the test is
   invalid. Do not proceed with a false red step.
6. Implement the smallest production-quality change that satisfies the task.
7. Run the focused test and confirm it passes.
8. Run related existing regression tests.
9. Run formatting/typechecking appropriate to changed languages.
10. Inspect `git diff --check` and `git diff`.
11. Remove generated junk, caches, secrets, and unrelated changes.
12. Update the progress file.
13. Commit using the task's specified commit message.
14. Continue directly to the next task.

Subagents may implement or review one bounded task at a time only if the required subagent workflow
is installed. Never allow two agents to edit the same files simultaneously. The primary agent must
review every subagent diff and rerun its verification before accepting it.

PLAN COMPLETION RULES

After every numbered plan:

1. Run that plan's complete completion gate.
2. Run all tests for every previously completed subsystem.
3. Confirm compatibility with the existing loop detector, CLI, demos, and prototype tests.
4. Update the progress file with the plan result and next plan.
5. Commit any required progress or integration corrections.
6. Push `feat/loopguard-production` without force:
   git push -u origin feat/loopguard-production
7. If `gh` is authenticated:
   - Create one draft PR after the first completed plan if none exists.
   - Otherwise update the existing draft PR body.
   - Do not create eleven unrelated PRs and do not merge the draft PR.
8. Continue immediately into the next plan unless a real blocker requires user authority.

The draft PR body must maintain:

- Completed plans and tasks
- Current plan/task
- Architecture decisions
- Exact test commands and results
- Baseline failures versus introduced failures
- Known limitations and blocked external verification
- Security-sensitive changes
- Migration or deployment requirements

BLOCKING CONDITIONS

Stop and ask the user only when:

- The working tree contains unrelated work that cannot be preserved safely.
- Git history diverged and a non-fast-forward or destructive choice is required.
- A plan requires a real external write, deployment, paid service, production credential, app-store
  action, GitHub App installation, or infrastructure authority not already provided.
- A database migration would destroy or reinterpret existing data.
- The repository and plan conflict in a way that materially changes product behavior.
- Required macOS/Xcode tooling for iOS verification is unavailable. Do not mark iOS complete or
  silently skip its required gates.
- A security boundary cannot be implemented as specified.

Do not stop for:

- A completed phase.
- Ordinary failing tests.
- Missing implementation.
- Refactoring required directly by the current task.
- Optional live model credentials when the plan specifies fake providers.
- Context length. Update the progress file and continue in a resumed session.

EXTERNAL-SIDE-EFFECT RULES

- Use fake providers, fake GitHub, local Docker fixtures, test databases, and local object storage
  unless the user explicitly authorizes a real integration.
- Do not deploy cloud infrastructure or publish apps.
- Do not send APNs notifications to real users.
- Do not install hooks globally on the user's machine during automated tests; test generated files
  under temporary directories.
- Do not open a real repair PR during tests.
- Do not expose a local unauthenticated network listener.
- Never commit credentials, tokens, local databases, build output, `.env` files, or generated
  secrets.

FINAL VERIFICATION

After all eleven plans:

1. Run every command in the master plan's final verification section.
2. Run all iOS unit and UI test schemes on the required iOS 26 simulator.
3. Run cloud migrations up, down where supported, and up again in an isolated test database.
4. Run security, tenant-isolation, replay, duplicate-action, browser-isolation, Auto-Heal, backup,
   restore, and release-verification gates.
5. Run `git diff --check`.
6. Confirm `git status --short` contains only intentional final changes.
7. Push the final integration branch and update the draft PR.
8. Do not merge or deploy.
9. Prepare production-readiness evidence, but stop at every named human sign-off, production
   apply/failover, billing-provider activation, App Store/TestFlight publication, and real
   customer notification gate. Report the exact human role and evidence required; never self-sign.

The final report must include:

- Every completed plan and task count
- Commit list grouped by plan
- Exact tests/checks with pass/fail counts
- Any gates that could not run and the precise reason
- Remaining production authority required from a human
- Migration and rollout instructions
- Security and privacy limitations
- Link to the draft PR

Never claim a plan, phase, or product is complete without fresh verification evidence.

START NOW

First report:

- Repository root and remotes
- Current branch and working-tree state
- Latest main commit
- Whether all spec/plan files exist
- Baseline commands and exact results
- The full eleven-plan execution order
- Any blocker requiring user input

If there is no blocker, create or resume `feat/loopguard-production`, initialize the progress file,
and begin Plan 1 Task 1. Continue top to bottom.
```
