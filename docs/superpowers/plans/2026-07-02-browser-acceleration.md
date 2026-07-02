# Browser and Playwright Acceleration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce browser startup and impacted-test latency while preserving per-session isolation and comprehensive merge gates.

**Architecture:** A version-pinned Node broker keeps browser server processes warm. An explicit
`@loopguard/playwright` fixture connects Playwright Test to that broker and creates a fresh isolated
`BrowserContext` per worker/session; a standalone `page.run` API is not counted as Playwright-test
acceleration. A Python client integrates lifecycle and selection with LoopGuard.

**Tech Stack:** Node.js 20+, TypeScript, Playwright, versioned bounded frames over Unix sockets or
Windows named pipes, Python asyncio client, pytest, Vitest

---

### Task 1: Define the browser broker protocol

**Files:**
- Create: `loopguard/integrations/browser-broker/package.json`
- Create: `loopguard/integrations/browser-broker/package-lock.json`
- Create: `loopguard/integrations/browser-broker/tsconfig.json`
- Create: `loopguard/integrations/browser-broker/src/protocol.ts`
- Create: `loopguard/integrations/browser-broker/src/protocol.test.ts`

- [ ] **Step 1: Write failing schema tests**

```ts
import { parseCommand } from "./protocol";

test("accepts an isolated context request", () => {
  expect(parseCommand({
    id: "1",
    method: "context.create",
    params: { sessionId: "s1", browser: "chromium", storageStatePath: null },
  }).method).toBe("context.create");
});

test("rejects unknown fields and browser names", () => {
  expect(() => parseCommand({
    id: "1", method: "context.create",
    params: { sessionId: "s1", browser: "netscape", secret: "x" },
  })).toThrow();
});
```

- [ ] **Step 2: Run the test and verify failure**

Run: `cd loopguard/integrations/browser-broker && npm test`
Expected: FAIL because the package/protocol is not implemented.

- [ ] **Step 3: Implement strict discriminated commands**

Use Zod schemas for:

```ts
type BrokerCommand =
  | { id: string; method: "context.create"; params: CreateContextParams }
  | { id: string; method: "context.close"; params: { contextId: string } }
  | { id: string; method: "page.run"; params: RunPageParams }
  | { id: string; method: "health"; params: Record<string, never> };
```

Responses contain `{id, ok, result}` or `{id, ok:false, error:{code,message}}`. Never place
cookies, storage-state contents, or page HTML in logs. Include protocol version and a random
daemon-issued connection capability in the handshake. Use bounded length-prefixed frames, reject
unknown fields/versions, cap pending requests and frame size, and make context capability IDs
unguessable and session-bound.

- [ ] **Step 4: Run protocol tests and typecheck**

Run:

```bash
cd loopguard/integrations/browser-broker
npm test
npx tsc --noEmit
```

Expected: both commands exit 0.

- [ ] **Step 5: Commit the broker protocol**

```bash
git add loopguard/integrations/browser-broker
git commit -m "feat: define isolated browser broker protocol"
```

### Task 2: Keep browsers warm and contexts isolated

**Files:**
- Create: `loopguard/integrations/browser-broker/src/broker.ts`
- Create: `loopguard/integrations/browser-broker/src/server.ts`
- Create: `loopguard/integrations/browser-broker/src/transport.ts`
- Create: `loopguard/integrations/browser-broker/src/broker.test.ts`
- Create: `loopguard/integrations/browser-broker/src/security.test.ts`

- [ ] **Step 1: Write failing lifecycle and isolation tests**

```ts
test("reuses browser but never context storage", async () => {
  const broker = await BrowserBroker.start({ browser: fakeBrowser });
  const a = await broker.createContext({ sessionId: "a" });
  await a.context.addCookies([{ name: "token", value: "secret-a",
    domain: "example.test", path: "/" }]);
  const b = await broker.createContext({ sessionId: "b" });
  expect(await b.context.cookies()).toEqual([]);
  expect(fakeBrowser.launchCount).toBe(1);
});

test("closing session destroys every page and context", async () => {
  const session = await broker.createContext({ sessionId: "a" });
  await session.context.newPage();
  await broker.closeContext(session.id);
  expect(session.context.isClosed()).toBe(true);
});
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard/integrations/browser-broker && npm test`
Expected: FAIL because `BrowserBroker` is missing.

- [ ] **Step 3: Implement warm process pools**

Maintain at most one browser process per configured browser/channel and one context per session.
Context creation must set a unique artifact directory, explicit locale/timezone, and disabled
service-worker policy unless configured. Context IDs are random capabilities, not sequential IDs.
Close contexts on session stop, idle TTL, broker shutdown, or client disconnect after grace period.

Create the Unix socket in the owner-only LoopGuard state directory, refuse symlink/wrong-owner
socket paths, set owner-only permissions, and require the daemon-issued connection capability
before any method. On Windows use a current-user-ACL named pipe and verify the caller through the
daemon-supervised capability. Bind every context/page/artifact request to the authenticated
session; one session cannot name another session's context capability.

Navigation defaults to the repository's configured origin allowlist. Deny `file:`, browser
extension, localhost metadata/admin ports, link-local/cloud-metadata endpoints, and private network
targets unless an explicit trusted browser policy allows them. Downloads and uploads remain inside
the session artifact directory. Test DNS rebinding, redirects to denied origins, context-ID
guessing, oversized frames, wrong capabilities, disconnect cleanup, and broker-process crash.

- [ ] **Step 4: Run broker tests**

Run: `cd loopguard/integrations/browser-broker && npm test`
Expected: PASS for storage, permissions, downloads, pages, artifact paths, idle cleanup, and
process restart.

- [ ] **Step 5: Commit warm isolated contexts**

```bash
git add loopguard/integrations/browser-broker
git commit -m "feat: reuse browsers with isolated contexts"
```

### Task 3: Add the Python broker client and daemon lifecycle

**Files:**
- Create: `loopguard/src/loopguard/browser/__init__.py`
- Create: `loopguard/src/loopguard/browser/client.py`
- Create: `loopguard/src/loopguard/browser/service.py`
- Test: `loopguard/tests/browser/test_client.py`
- Modify: `loopguard/src/loopguard/control/daemon.py`

- [ ] **Step 1: Write failing restart and timeout tests**

```python
from loopguard.browser.client import BrowserBrokerClient


def test_client_restarts_broker_after_process_exit(fake_broker_factory):
    client = BrowserBrokerClient(factory=fake_broker_factory)
    first = run(client.health())
    fake_broker_factory.current.exit()
    second = run(client.health())
    assert first.ok and second.ok
    assert fake_broker_factory.starts == 2


def test_page_command_has_hard_timeout(fake_broker_factory):
    client = BrowserBrokerClient(factory=fake_broker_factory)
    fake_broker_factory.current.hang = True
    assert run(client.run_page("ctx", actions=[], timeout_seconds=1)).code == "timeout"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/browser/test_client.py`
Expected: FAIL because the browser package is absent.

- [ ] **Step 3: Implement supervised framed communication**

Start the pinned broker with an argument array and inherited one-time capability. Correlate
response IDs, validate every response, bound frames/pending requests, and restart only after
backoff. Persist no raw connection capability. Daemon shutdown must close the broker cleanly.
Health reports browser versions and active context counts, never session secrets.

Register `BrowserService` in `DaemonServices`; session lifecycle events create/close browser
leases, and daemon restart marks lost contexts so callers reconnect rather than reusing stale IDs.

- [ ] **Step 4: Run Python client tests**

Run: `cd loopguard && python -m pytest -q tests/browser/test_client.py`
Expected: PASS.

- [ ] **Step 5: Commit broker supervision**

```bash
git add loopguard/src/loopguard/browser loopguard/tests/browser \
  loopguard/src/loopguard/control/daemon.py
git commit -m "feat: supervise local browser broker"
```

### Task 4: Reuse authentication through explicit setup artifacts

**Files:**
- Create: `loopguard/src/loopguard/browser/auth.py`
- Test: `loopguard/tests/browser/test_auth.py`
- Create: `loopguard/integrations/browser-broker/src/storage.ts`
- Create: `loopguard/integrations/browser-broker/src/storage.test.ts`

- [ ] **Step 1: Write failing ownership tests**

```python
def test_storage_state_is_scoped_to_repo_profile_and_environment(auth_store):
    key = auth_store.save(
        repo_id="r1", profile="admin", environment="staging", body=b"encrypted"
    )
    assert auth_store.load("r1", "admin", "staging").key == key
    assert auth_store.load("r2", "admin", "staging") is None
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/browser/test_auth.py`
Expected: FAIL because the auth store is missing.

- [ ] **Step 3: Implement explicit encrypted storage-state references**

Store state outside the repository under LoopGuard home. Encrypt with a locally stored platform
key. Index by tenant/user, repo, profile, environment, and origin allowlist. The broker receives a
temporary decrypted file path with restrictive permissions and deletes it after context creation.
Never infer that production and staging credentials are interchangeable.

Create decrypted files in an owner-only dedicated directory with random names and open them using
no-follow/exclusive semantics. Record only a non-secret lease marker. On normal close, broker
crash, daemon restart, failed context creation, timeout, or cancellation, securely unlink every
leased plaintext file before accepting new work. Tests scan the state/temp directories after each
failure and prove no cookie/token bytes remain. Never copy storage state into traces, screenshots,
logs, exception messages, or repair artifacts.

- [ ] **Step 4: Run Python and Node storage tests**

Run:

```bash
cd loopguard
python -m pytest -q tests/browser/test_auth.py
cd integrations/browser-broker
npm test
```

Expected: all tests pass.

- [ ] **Step 5: Commit scoped authentication reuse**

```bash
git add loopguard/src/loopguard/browser loopguard/tests/browser \
  loopguard/integrations/browser-broker
git commit -m "feat: reuse scoped browser authentication safely"
```

### Task 5: Select impacted Playwright tests

**Files:**
- Create: `loopguard/src/loopguard/browser/manifest.py`
- Create: `loopguard/src/loopguard/browser/selection.py`
- Test: `loopguard/tests/browser/test_selection.py`
- Create: `loopguard/scripts/playwright_manifest.ts`

- [ ] **Step 1: Write failing selection tests**

```python
from loopguard.browser.selection import PlaywrightSelector


def test_route_change_selects_route_and_shared_setup(playwright_manifest):
    selector = PlaywrightSelector(playwright_manifest)
    result = selector.select(changed_paths=["app/login/page.tsx"])
    assert result.test_files == ["e2e/auth.setup.ts", "e2e/login.spec.ts"]
    assert result.explanations["e2e/login.spec.ts"] == ["covers-route:/login"]


def test_unknown_shared_change_falls_back_to_smoke(playwright_manifest):
    result = PlaywrightSelector(playwright_manifest).select(["src/theme.ts"])
    assert result.projects == ["smoke"]
    assert result.confidence == "fallback"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/browser/test_selection.py`
Expected: FAIL because selection is missing.

- [ ] **Step 3: Implement manifest generation and conservative fallback**

The TypeScript script uses Playwright's test discovery plus `.loopguard/browser.toml` mappings to
emit test file, project, tags, dependencies, route annotations, and component annotations. The
selector combines manifest edges with the context import graph. Shared infrastructure, test
configuration, global setup, and unknown high-impact changes select the configured smoke project
rather than zero tests.

- [ ] **Step 4: Run selection tests**

Run: `cd loopguard && python -m pytest -q tests/browser/test_selection.py`
Expected: PASS.

- [ ] **Step 5: Commit impacted browser selection**

```bash
git add loopguard/src/loopguard/browser loopguard/tests/browser \
  loopguard/scripts/playwright_manifest.ts
git commit -m "feat: select impacted playwright tests"
```

### Task 6: Add trace and execution policies

**Files:**
- Create: `loopguard/src/loopguard/browser/execution.py`
- Test: `loopguard/tests/browser/test_execution.py`
- Modify: `loopguard/src/loopguard/verify/plugins.py`
- Create: `loopguard/integrations/playwright-loopguard/package.json`
- Create: `loopguard/integrations/playwright-loopguard/package-lock.json`
- Create: `loopguard/integrations/playwright-loopguard/tsconfig.json`
- Create: `loopguard/integrations/playwright-loopguard/src/fixture.ts`
- Create: `loopguard/integrations/playwright-loopguard/src/fixture.test.ts`
- Create: `loopguard/integrations/playwright-loopguard/src/config.ts`

- [ ] **Step 1: Write failing phase-policy tests**

```python
def test_iteration_runs_impacted_chromium_without_trace(browser_policy):
    plan = browser_policy.plan(phase="impacted", failures=0)
    assert plan.projects == ["chromium"]
    assert plan.trace == "off"


def test_first_retry_records_trace(browser_policy):
    plan = browser_policy.plan(phase="impacted", failures=1)
    assert plan.trace == "on-first-retry"


def test_pr_gate_runs_configured_full_suite(browser_policy):
    plan = browser_policy.plan(phase="pr", failures=0)
    assert plan.selection == "full"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/browser/test_execution.py`
Expected: FAIL because execution policy is missing.

- [ ] **Step 3: Implement explicit execution plans**

Generate argv arrays, never shell strings. Iteration runs impacted tests and one primary browser.
Completion runs impacted tests across required configured projects. PR phase runs the repository
full gate and permits CI sharding. Trace defaults to first retry or retain-on-failure; `trace=on`
requires explicit diagnostic mode because of overhead.

Implement and package the explicit `@loopguard/playwright` fixture/config adapter. It authenticates
to the local broker, obtains a browser-server endpoint capability, connects through Playwright's
supported client API, and creates a new context with the test project's options for each
worker/session. It closes pages/context/connection in fixture teardown and on cancellation. The
fixture is opt-in through the project's `playwright.config.ts`; LoopGuard doctor reports whether
the project actually imports it. A `page.run` RPC is useful for LoopGuard-owned probes but does not
count as accelerating ordinary `npx playwright test`.

Add integration fixtures that run a real two-test Playwright project twice: native configuration
and LoopGuard fixture. Assert browser/context isolation, correct Playwright fixtures/reporters/
traces, process reuse across separate CLI invocations, cleanup after failing tests, and fallback to
native Playwright when the broker is unavailable. Do not require application teams to rewrite test
bodies.

- [ ] **Step 4: Run browser and verification integration tests**

Run:

```bash
cd loopguard
python -m pytest -q tests/browser tests/verify/test_impact.py
cd integrations/playwright-loopguard
npm ci
npm test
npx tsc --noEmit
```
Expected: PASS.

- [ ] **Step 5: Commit browser execution policy**

```bash
git add loopguard/src/loopguard/browser loopguard/src/loopguard/verify \
  loopguard/tests/browser loopguard/integrations/playwright-loopguard
git commit -m "feat: accelerate browser verification by phase"
```

### Task 7: Measure performance without weakening correctness

**Files:**
- Create: `loopguard/src/loopguard/browser/metrics.py`
- Test: `loopguard/tests/browser/test_metrics.py`
- Create: `loopguard/docs/browser-performance.md`

- [ ] **Step 1: Write failing metric-separation tests**

```python
def test_metrics_separate_cold_warm_impacted_and_full(recorder):
    recorder.observe("cold_start_ms", 1800)
    recorder.observe("warm_context_ms", 70)
    recorder.observe("impacted_suite_ms", 2300)
    recorder.observe("full_suite_ms", 47000)
    report = recorder.report()
    assert set(report) == {
        "cold_start_ms", "warm_context_ms", "impacted_suite_ms", "full_suite_ms"
    }
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/browser/test_metrics.py`
Expected: FAIL because metrics are missing.

- [ ] **Step 3: Implement histograms and correctness counters**

Record p50/p95 duration, selected/total test counts, cache hit, retry, trace bytes, and whether the
full gate later found a failure missed by impacted selection. Report speed and selection escapes
together; never report latency improvement without the escape metric.

Benchmark against Playwright Test's native browser reuse within a worker, not against launching a
fresh browser for every test. Measure cold first run, warm repeated invocation, impacted suite,
full suite, and broker-unavailable fallback on the same pinned browser/version/machine fixture.
Separate savings from test selection versus cross-invocation browser reuse. Record at least 30
samples after warm-up and publish distribution/variance, not a single best run.

Enable the broker fixture automatically only when it shows a configured material p50/p95
improvement without isolation failures, reporter/trace regressions, or a higher full-gate escape
rate. Otherwise ship it disabled and retain the measured result; never claim “faster Playwright”
from the standalone probe API alone.

- [ ] **Step 4: Run complete browser tests**

Run: `cd loopguard && python -m pytest -q tests/browser`
Expected: PASS.

- [ ] **Step 5: Commit browser performance measurement**

```bash
git add loopguard/src/loopguard/browser loopguard/tests/browser \
  loopguard/docs/browser-performance.md
git commit -m "feat: measure browser speed and selection safety"
```

## Completion gate

Run:

```bash
cd loopguard/integrations/browser-broker
npm ci
npm test
npx tsc --noEmit
cd ../playwright-loopguard
npm ci
npm test
npx tsc --noEmit
cd ../..
python -m pytest -q tests/browser
```

Expected: all commands exit 0; browser processes are reused; contexts remain isolated; and final
merge verification never relies only on impacted-test selection. A real Playwright Test project
uses the fixture, broker security/origin policy passes on Unix and Windows transports, crash
cleanup leaves no decrypted auth state, and published speed claims are supported by the native
Playwright baseline benchmark.
