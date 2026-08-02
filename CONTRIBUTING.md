# Contributing to LoopGuard

Thank you for helping make AI coding sessions safer. LoopGuard accepts focused bug fixes, tests,
documentation, adapters, and operational improvements. Keep changes small enough to review and
never include real prompts, source, credentials, customer data, or production identifiers.

## Local setup

Use Python 3.11 or newer and Node.js 20.9 or newer.

```bash
cd loopguard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[all-dev]"
python -m pytest -q
```

For the hosted API:

```bash
cd services/control-api
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "../../loopguard[control,heal,telemetry]" -e ".[dev]"
python -m pytest -q
```

For the web console:

```bash
cd apps/web
npm ci
npm run generate:docs
npm run typecheck
npm test
npm run build
```

The iOS project is under `apps/ios`. Open `LoopGuard.xcodeproj`, select the `LoopGuard` scheme, and
use an iOS 26 simulator. No production service or credential is required by the test schemes.

## Change workflow

1. Start from an up-to-date branch.
2. Add a failing test for behavior changes.
3. Implement the smallest complete change.
4. Run the focused test and the owning package's full suite.
5. Regenerate CLI, OpenAPI, and public-doc indexes when their source changes.
6. Explain security boundaries, migrations, rollout, and rollback in the pull request.

Useful cumulative checks:

```bash
cd loopguard && python -m ruff check src tests && python -m pytest -q -W error::RuntimeWarning
cd services/control-api && python -m ruff check src tests scripts && python -m pytest -q
cd apps/web && npm run generate:docs && npm run typecheck && npm run lint && npm test && npm run build
git diff --check
```

Docker, PostgreSQL, k6, Terraform, Helm, browser, and iOS gates are required when the changed area
uses them. A skipped tool is reported in the pull request; a skip is not a pass.

## Pull requests

Use the template. Link the issue or plan task, include exact command results, and call out generated
files. Do not mix unrelated formatting with behavior changes. Maintainers may ask for a threat-model
update when code changes authentication, authorization, signing, tenant data, repair execution,
billing, support access, release provenance, or retention.

The project has no approved distribution license yet. Contributions are reviewable source changes,
not permission to publish packages or reuse the repository under terms that do not exist. That legal
gate remains `awaiting_owner_legal_choice`.

See [SECURITY.md](SECURITY.md) for private vulnerability reporting and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for participation expectations.
