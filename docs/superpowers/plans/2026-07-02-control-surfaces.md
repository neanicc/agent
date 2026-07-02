# Native iOS and Web Control Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished native iOS safety inbox and a dense web operations console for sessions, changes, verification, repairs, policies, costs, devices, and audit.

**Architecture:** FastAPI publishes a versioned OpenAPI contract. The web app and iOS app generate typed clients but own separate presentation layers. Both use cursor replay, optimistic state only for non-destructive UI, and server-confirmed state for approvals.

**Tech Stack:** Swift 6, SwiftUI/iOS 26, Observation, URLSession, Keychain, APNs, ActivityKit, Next.js App Router, React, TypeScript, TanStack Query, Playwright, Vitest

---

### Task 1: Publish and validate the client API contract

**Files:**
- Create: `services/control-api/scripts/export_openapi.py`
- Create: `contracts/control-api.openapi.json`
- Create: `contracts/fixtures/session-stream.jsonl`
- Create: `contracts/fixtures/action-states.json`
- Test: `services/control-api/tests/test_openapi_contract.py`

- [ ] **Step 1: Write failing required-operation tests**

```python
def test_openapi_contains_required_control_operations(app):
    paths = app.openapi()["paths"]
    assert "/v1/sessions" in paths
    assert "/v1/sessions/{session_id}" in paths
    assert "/v1/actions" in paths
    assert "/v1/actions/{action_id}" in paths
    assert "/v1/repairs/{repair_id}" in paths
    assert "/v1/preferences" in paths
    assert "/v1/audit" in paths
```

- [ ] **Step 2: Verify missing operations**

Run: `cd services/control-api && python -m pytest -q tests/test_openapi_contract.py`
Expected: FAIL until all client-facing routes and schemas are registered.

- [ ] **Step 3: Implement contract export and compatibility check**

Export sorted JSON with build-time schema version. Add a compatibility test that rejects removal of
an operation, required response field, or enum member without a major contract-version change.
Fixtures cover stream replay, pending action, completed action, stale action, regression,
verification success, repair candidates, and artifact references.

- [ ] **Step 4: Export and validate**

Run:

```bash
cd services/control-api
python scripts/export_openapi.py ../../contracts/control-api.openapi.json
python -m pytest -q tests/test_openapi_contract.py
```

Expected: PASS and no uncommitted contract drift after a second export.

- [ ] **Step 5: Commit client contracts**

```bash
git add services/control-api contracts
git commit -m "feat: publish versioned control api contract"
```

### Task 2: Scaffold the web application and typed client

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/next.config.ts`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/src/app/layout.tsx`
- Create: `apps/web/src/app/page.tsx`
- Create: `apps/web/src/lib/api/generated.ts`
- Create: `apps/web/src/lib/api/client.ts`
- Create: `apps/web/src/lib/api/client.test.ts`

- [ ] **Step 1: Write failing auth and error tests**

```ts
import { ControlApiClient } from "./client";

test("sends access token and request id", async () => {
  const fetcher = vi.fn().mockResolvedValue(ok({ items: [] }));
  const client = new ControlApiClient({ baseUrl: "https://api.test", token: () => "token", fetcher });
  await client.sessions();
  expect(fetcher.mock.calls[0][1].headers.Authorization).toBe("Bearer token");
  expect(fetcher.mock.calls[0][1].headers["X-Request-ID"]).toBeTruthy();
});

test("maps stale action response to typed error", async () => {
  const fetcher = vi.fn().mockResolvedValue(problem(409, "stale_state"));
  await expect(new ControlApiClient(options(fetcher)).createAction(action())).rejects
    .toMatchObject({ code: "stale_state" });
});
```

- [ ] **Step 2: Verify test failure**

Run: `cd apps/web && npm test`
Expected: FAIL because the app/client is absent.

- [ ] **Step 3: Generate types and implement the client wrapper**

Use `openapi-typescript` against `contracts/control-api.openapi.json`. The wrapper owns auth,
request IDs, RFC 9457 problem mapping, cursor parameters, and abort signals. It does not add silent
retries to action creation. Set up App Router, strict TypeScript, ESLint, Vitest, and Playwright.

- [ ] **Step 4: Run unit tests and typecheck**

Run:

```bash
cd apps/web
npm test
npx tsc --noEmit
```

Expected: both commands exit 0.

- [ ] **Step 5: Commit the web foundation**

```bash
git add apps/web
git commit -m "feat: scaffold typed web control console"
```

### Task 3: Build the web information architecture and live session timeline

**Files:**
- Create: `apps/web/src/app/(console)/layout.tsx`
- Create: `apps/web/src/app/(console)/inbox/page.tsx`
- Create: `apps/web/src/app/(console)/runs/page.tsx`
- Create: `apps/web/src/app/(console)/runs/[id]/page.tsx`
- Create: `apps/web/src/components/console-shell.tsx`
- Create: `apps/web/src/components/session-timeline.tsx`
- Create: `apps/web/src/lib/api/session-stream.ts`
- Test: `apps/web/src/components/session-timeline.test.tsx`
- Test: `apps/web/e2e/session-reconnect.spec.ts`

- [ ] **Step 1: Write failing timeline reducer tests**

```ts
test("replay and live duplicate event render once", () => {
  let state = initialTimeline();
  state = reduceTimeline(state, event({ cursor: 4, event_id: "e4" }));
  state = reduceTimeline(state, event({ cursor: 4, event_id: "e4" }));
  expect(state.items).toHaveLength(1);
  expect(state.cursor).toBe(4);
});

test("cursor gap requests replay before applying live event", () => {
  const state = reduceTimeline(timelineAt(4), event({ cursor: 6, event_id: "e6" }));
  expect(state.status).toBe("resyncing");
  expect(state.missingAfter).toBe(4);
});
```

- [ ] **Step 2: Verify failure**

Run: `cd apps/web && npm test -- session-timeline.test.tsx`
Expected: FAIL because timeline state is absent.

- [ ] **Step 3: Implement shell, routes, and replay-safe streaming**

Navigation: Inbox, Runs, Changes, Verification, Repairs, Costs, Policies, Devices, Audit. The run
detail groups events into turns and phase boundaries, shows model/effort and cost separately, and
keeps raw tool output behind disclosure. Reconnect with the last applied cursor and fetch gaps
before rendering newer live data.

- [ ] **Step 4: Run unit and reconnect tests**

Run:

```bash
cd apps/web
npm test -- session-timeline.test.tsx
npx playwright test e2e/session-reconnect.spec.ts
```

Expected: PASS.

- [ ] **Step 5: Commit the live web console**

```bash
git add apps/web
git commit -m "feat: add replay-safe web session console"
```

### Task 4: Implement safe web approval and repair surfaces

**Files:**
- Create: `apps/web/src/components/action-sheet.tsx`
- Create: `apps/web/src/components/verification-proof.tsx`
- Create: `apps/web/src/components/repair-candidate-matrix.tsx`
- Create: `apps/web/src/app/(console)/repairs/[id]/page.tsx`
- Test: `apps/web/src/components/action-sheet.test.tsx`
- Test: `apps/web/e2e/action-expiry.spec.ts`

- [ ] **Step 1: Write failing explicit-effect tests**

```ts
test("approval shows target effect risk and expiry before submission", () => {
  render(<ActionSheet request={approveFixRequest()} />);
  expect(screen.getByText("Session auth-migration")).toBeVisible();
  expect(screen.getByText("Inject the verified correction and continue")).toBeVisible();
  expect(screen.getByText(/expires/i)).toBeVisible();
  expect(screen.getByRole("button", { name: "Approve correction" })).toBeEnabled();
});

test("expired request disables action", () => {
  render(<ActionSheet request={expiredRequest()} />);
  expect(screen.getByRole("button", { name: /expired/i })).toBeDisabled();
});
```

- [ ] **Step 2: Verify failure**

Run: `cd apps/web && npm test -- action-sheet.test.tsx`
Expected: FAIL because the approval UI is absent.

- [ ] **Step 3: Implement server-confirmed actions**

Never mark an action successful until the API returns resolution. Disable duplicate submission by
action ID, but retain retry for network failure after fetching current action state. Repair detail
shows reproduction evidence, candidate diffs, exact checks, ranking reason, data-contract impact,
and rollback before enabling draft-PR publication.

- [ ] **Step 4: Run action unit/E2E tests**

Run:

```bash
cd apps/web
npm test -- action-sheet.test.tsx
npx playwright test e2e/action-expiry.spec.ts
```

Expected: PASS.

- [ ] **Step 5: Commit safe web actions**

```bash
git add apps/web
git commit -m "feat: add explicit web approval and repair flows"
```

### Task 5: Scaffold the native iOS application

**Files:**
- Create: `apps/ios/project.yml`
- Create: `apps/ios/LoopGuard/App/LoopGuardApp.swift`
- Create: `apps/ios/LoopGuard/App/AppModel.swift`
- Create: `apps/ios/LoopGuard/API/ControlAPI.swift`
- Create: `apps/ios/LoopGuard/API/APIModels.swift`
- Create: `apps/ios/LoopGuard/Security/KeychainStore.swift`
- Create: `apps/ios/LoopGuardTests/ControlAPITests.swift`
- Create: `apps/ios/LoopGuardTests/KeychainStoreTests.swift`

- [ ] **Step 1: Write failing authenticated request tests**

```swift
@Test func requestAddsBearerAndRequestID() async throws {
    let transport = RecordingTransport(response: .json(["items": []]))
    let api = ControlAPI(baseURL: URL(string: "https://api.test")!,
                         tokenProvider: { "token" }, transport: transport)
    _ = try await api.sessions(after: nil)
    #expect(transport.lastRequest?.value(forHTTPHeaderField: "Authorization") == "Bearer token")
    #expect(transport.lastRequest?.value(forHTTPHeaderField: "X-Request-ID") != nil)
}
```

- [ ] **Step 2: Generate project and verify test failure**

Run:

```bash
cd apps/ios
xcodegen generate
xcodebuild test -scheme LoopGuard -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

Expected: FAIL until the API types and transport are implemented.

- [ ] **Step 3: Implement app shell, API transport, and Keychain tokens**

Use Swift Observation with one `@MainActor @Observable AppModel`. `ControlAPI` uses async
`URLSession`, RFC problem decoding, request IDs, and cancellation. Store refresh/access
credentials only in Keychain with after-first-unlock device-only accessibility. Do not persist
event payloads containing source code unless offline mode explicitly enables encrypted cache.

- [ ] **Step 4: Run iOS unit tests**

Run through XcodeBuildMCP with the generated project, `LoopGuard` scheme, and an iOS 26 simulator.
Expected: all unit tests PASS.

- [ ] **Step 5: Commit native iOS foundation**

```bash
git add apps/ios
git commit -m "feat: scaffold native loopguard ios app"
```

### Task 6: Build the iOS safety inbox and run detail

**Files:**
- Create: `apps/ios/LoopGuard/Features/Inbox/InboxView.swift`
- Create: `apps/ios/LoopGuard/Features/Inbox/InboxViewModel.swift`
- Create: `apps/ios/LoopGuard/Features/Runs/RunListView.swift`
- Create: `apps/ios/LoopGuard/Features/Runs/RunDetailView.swift`
- Create: `apps/ios/LoopGuard/Features/Runs/TimelineReducer.swift`
- Create: `apps/ios/LoopGuardTests/TimelineReducerTests.swift`
- Create: `apps/ios/LoopGuardUITests/InboxUITests.swift`

- [ ] **Step 1: Write failing timeline and priority tests**

```swift
@Test func duplicateCursorIsIgnored() {
    var state = TimelineState()
    state.apply(.fixture(cursor: 3, id: "e3"))
    state.apply(.fixture(cursor: 3, id: "e3"))
    #expect(state.items.count == 1)
    #expect(state.cursor == 3)
}

@Test func inboxOrdersBlockingBeforeInformational() {
    let sorted = InboxItem.sort([.completed, .regression, .loop])
    #expect(sorted.map(\.kind) == [.regression, .loop, .completed])
}
```

- [ ] **Step 2: Run focused tests and verify failure**

Run through XcodeBuildMCP, filtering to `TimelineReducerTests` and `InboxUITests`.
Expected: FAIL because features are absent.

- [ ] **Step 3: Implement native hierarchy**

Use a `TabView` with Inbox, Runs, Changes, Repairs, and Settings. The Inbox shows only items that
need attention plus quiet completion summaries. Run detail shows phase, agent, model/effort, cost,
verification, and a compact timeline. Apply Liquid Glass to tab/navigation/control surfaces; use
standard materials for content cards. Support Dynamic Type, VoiceOver labels, reduced motion,
high contrast, and keyboard navigation on iPad.

- [ ] **Step 4: Run unit/UI tests and inspect simulator screenshots**

Run all iOS tests through XcodeBuildMCP, launch in the simulator, capture Inbox and Run Detail in
light/dark mode and at an accessibility text size.
Expected: tests PASS with no clipped controls or unreadable contrast.

- [ ] **Step 5: Commit iOS monitoring surfaces**

```bash
git add apps/ios
git commit -m "feat: add ios safety inbox and run timeline"
```

### Task 7: Implement iOS actions, notifications, and Live Activities

**Files:**
- Create: `apps/ios/LoopGuard/Features/Actions/ActionReviewView.swift`
- Create: `apps/ios/LoopGuard/Features/Actions/ActionViewModel.swift`
- Create: `apps/ios/LoopGuard/Notifications/NotificationManager.swift`
- Create: `apps/ios/LoopGuard/Activities/RunActivityAttributes.swift`
- Create: `apps/ios/LoopGuardTests/ActionViewModelTests.swift`
- Create: `apps/ios/LoopGuardUITests/ActionReviewUITests.swift`

- [ ] **Step 1: Write failing expiry and duplicate tests**

```swift
@Test func expiredActionCannotSubmit() async {
    let model = ActionViewModel(request: .expiredFixture(), api: .recording())
    await model.submit()
    #expect(model.state == .expired)
    #expect(model.api.submittedActionIDs.isEmpty)
}

@Test func doubleTapSubmitsOnce() async {
    let api = RecordingControlAPI()
    let model = ActionViewModel(request: .activeFixture(), api: api)
    async let first: Void = model.submit()
    async let second: Void = model.submit()
    _ = await (first, second)
    #expect(api.submittedActionIDs.count == 1)
}
```

- [ ] **Step 2: Verify failure**

Run focused iOS tests through XcodeBuildMCP.
Expected: FAIL because action flow is absent.

- [ ] **Step 3: Implement explicit confirmation and notification routing**

Show target, effect, risk, parameters, expected state, and countdown. Require biometric
confirmation for organization-configured high-risk actions. APNs payloads carry only action or
session identifiers and generic text; fetch sensitive detail after authentication. Live Activity
shows non-sensitive phase/progress and deep-links to run detail.

- [ ] **Step 4: Run unit/UI tests and push-notification fixture tests**

Run all iOS tests through XcodeBuildMCP and deliver local notification fixtures in Simulator.
Expected: PASS; expired/deep-linked actions fetch current state before enabling controls.

- [ ] **Step 5: Commit iOS action flows**

```bash
git add apps/ios
git commit -m "feat: add secure ios approvals and notifications"
```

### Task 8: Add policies, costs, devices, and audit to web

**Files:**
- Create: `apps/web/src/app/(console)/policies/page.tsx`
- Create: `apps/web/src/app/(console)/costs/page.tsx`
- Create: `apps/web/src/app/(console)/devices/page.tsx`
- Create: `apps/web/src/app/(console)/audit/page.tsx`
- Create: `apps/web/src/components/policy-editor.tsx`
- Test: `apps/web/src/components/policy-editor.test.tsx`
- Test: `apps/web/e2e/tenant-boundaries.spec.ts`

- [ ] **Step 1: Write failing policy validation tests**

```ts
test("cannot lower managed safety rule", async () => {
  render(<PolicyEditor profile={managedProfile()} />);
  await userEvent.selectOptions(screen.getByLabelText("wcag-contrast severity"), "inform");
  expect(screen.getByText("Managed safety rules cannot be weakened")).toBeVisible();
  expect(screen.getByRole("button", { name: "Save policy" })).toBeDisabled();
});
```

- [ ] **Step 2: Verify failure**

Run: `cd apps/web && npm test -- policy-editor.test.tsx`
Expected: FAIL because policy UI is absent.

- [ ] **Step 3: Implement administrative surfaces**

Policy editor validates locally and server-side, renders source/precedence, and previews affected
capabilities. Cost views separate agent, judge, verification, critic, and repair cost and label
counterfactual estimates. Device management supports revoke and last-seen data. Audit is
filterable/exportable and never editable.

- [ ] **Step 4: Run web unit and tenant E2E tests**

Run:

```bash
cd apps/web
npm test
npx tsc --noEmit
npx playwright test e2e/tenant-boundaries.spec.ts
```

Expected: PASS.

- [ ] **Step 5: Commit administrative console**

```bash
git add apps/web
git commit -m "feat: add policy cost device and audit console"
```

### Task 9: Establish visual, accessibility, and migration gates

**Files:**
- Create: `apps/web/e2e/accessibility.spec.ts`
- Create: `apps/web/e2e/visual.spec.ts`
- Create: `apps/ios/LoopGuardUITests/AccessibilityUITests.swift`
- Create: `docs/product/control-surfaces.md`
- Modify: `cloud-app/README.md`

- [ ] **Step 1: Add failing accessibility assertions**

Web:

```ts
test("inbox has no serious axe violations", async ({ page }) => {
  await page.goto("/inbox");
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter(v => ["serious", "critical"].includes(v.impact ?? ""))).toEqual([]);
});
```

iOS UI tests must assert every actionable element has a non-empty accessibility label and the
primary flows remain hittable at the largest supported content size.

- [ ] **Step 2: Run web and iOS accessibility tests**

Expected: FAIL until violations and labels are corrected.

- [ ] **Step 3: Fix all violations and document migration**

Capture stable visual references for Inbox, Run Detail, Action Review, Verification, and Repair in
light/dark modes. Document how features move from `cloud-app`, mark the Expo app deprecated after
parity, and retain it for one release as a fallback. Do not delete it in this plan.

- [ ] **Step 4: Run complete surface verification**

Run:

```bash
cd apps/web
npm test
npx tsc --noEmit
npx playwright test
```

Run all iOS unit/UI tests and capture simulator screenshots through XcodeBuildMCP.
Expected: all checks PASS.

- [ ] **Step 5: Commit the release-quality gates**

```bash
git add apps/web apps/ios cloud-app/README.md docs/product/control-surfaces.md
git commit -m "test: enforce control surface quality"
```

## Completion gate

Web:

```bash
cd apps/web
npm ci
npm test
npx tsc --noEmit
npx playwright test
```

iOS: build and run all unit/UI tests with XcodeBuildMCP on an iOS 26 simulator, then inspect
light/dark and accessibility-size screenshots.

Expected: all checks pass; actions are replay-safe; sensitive content is absent from push payloads;
and content surfaces use native hierarchy rather than indiscriminate glass styling.
