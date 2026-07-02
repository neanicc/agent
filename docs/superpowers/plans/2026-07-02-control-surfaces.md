# Native iOS and Web Control Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished native iOS safety inbox and a dense web operations console for sessions, changes, verification, repairs, policies, costs, devices, and audit.

**Architecture:** FastAPI publishes a versioned OpenAPI contract. The web app and iOS app generate typed clients but own separate presentation layers. Both use cursor replay, optimistic state only for non-destructive UI, and server-confirmed state for approvals.

**Tech Stack:** Swift 6, SwiftUI/iOS 26, Observation, URLSession, Keychain, APNs, ActivityKit, Next.js App Router, React, TypeScript, TanStack Query, Playwright, Vitest

---

## Locked product-design contract

The production clients reuse the Expo prototype's operational-workbench vocabulary without
copying its implementation:

- **Shared:** cool neutral surfaces, one cobalt navigation/focus accent, semantic status colours
  paired with text/icons, restrained radii, dense rows, direct verb-first actions, and live product
  state as the visual anchor. Cards exist only for bounded interactive objects such as an approval
  or repair candidate.
- **Web:** Space Grotesk for display, IBM Plex Sans for interface text, and IBM Plex Mono only for
  telemetry/code. Use semantic light and dark tokens derived from `cloud-app/tokens.css`; no raw
  colours in components.
- **Native iOS:** system typography and SF Symbols so Dynamic Type, language fallback, and platform
  metrics remain correct. Use Liquid Glass only for iOS 26 navigation and transient controls.
  Content stays on standard grouped surfaces; the older Expo rule against glass still applies to
  the prototype and to content panels.
- **Motion:** state crossfades and direct manipulation feedback only. Reduced Motion removes
  spatial movement and limits opacity transitions to 150 ms.
- **Quality bar:** no dashboard-card mosaic, decorative gradient, coloured icon circle, ambient
  blob, fake browser chrome, emoji icon, all-centred hierarchy, or copy that describes the design.
  The interface must remain premium with all decorative shadows removed.

The implementer creates `docs/product/design-system.md` before feature screens. It contains the
semantic light/dark tokens, type rules, spacing, radii, status grammar, icon rules, component
inventory, content voice, and explicit web/iOS divergence above. `cloud-app/design.md` remains the
prototype source and is not silently redefined.

### Information architecture and priority

```text
WEB (wide)                         IOS (phone)
Console shell                     TabView
├─ Inbox (attention queue)        ├─ Inbox (attention queue)
├─ Runs                           ├─ Runs
│  └─ Run detail                  │  └─ Run detail
│     1. current state/action     ├─ Changes
│     2. verification proof       │  └─ Change detail + proof
│     3. timeline/raw evidence    ├─ Repairs
├─ Changes                        │  └─ Repair detail + publish action
├─ Verification                  └─ Settings
├─ Repairs                           ├─ Account/devices
├─ Costs                             ├─ Notifications
├─ Policies                          └─ Local/privacy controls
├─ Devices
└─ Audit
```

For every operational screen, the first three scan targets are: **what needs attention**, **what
proof explains it**, and **what safe action is available**. Developer metadata and raw tool output
remain behind progressive disclosure. Inbox is not a second dashboard; it is a finite,
priority-ordered work queue.

### Responsive behavior

| Viewport | Navigation | Content behavior |
|---|---|---|
| Web `>= 1280px` | Persistent labelled sidebar | Two-pane list/detail where it reduces navigation; proof remains adjacent to the selected item |
| Web `768-1279px` | Compact icon rail with accessible labels/tooltips | Single primary pane plus dismissible inspector; tables pin identity/status and allow horizontal evidence scroll |
| Web `< 768px` | Top app bar plus full-height navigation sheet | Single-column queue/detail; actions use a sticky safe-area footer; dense tables become labelled row groups, never hidden columns |
| iPhone | Five-tab native `TabView` | Navigation stacks per tab; one-handed primary action above the safe area; no hover-only affordance |
| iPad | Sidebar-capable split view | List/detail layout, hardware-keyboard focus order, commands, and pointer states |

Web touch targets are at least 44 CSS px on coarse pointers and compact rows remain at least 32 px
with keyboard focus on fine pointers. No control is discoverable only on hover. Long repository,
branch, model, and file names truncate visually but expose the full value through an accessible
label and copy action.

### Interaction state contract

| Surface | Loading | Empty | Error | Success | Partial/stale |
|---|---|---|---|---|---|
| Inbox | Row skeletons preserving queue geometry | “No action needed” plus last sync and link to Runs | Inline retry with request ID; cached items remain labelled stale | Resolved item leaves queue and an undo-free receipt is announced | Offline/stale banner; destructive controls disabled |
| Run list/detail | Header and timeline placeholders | First-run explanation plus copyable start command | Preserve last valid state, show retry and diagnostics disclosure | Current phase, proof verdict, and completion receipt | Cursor gap shows “Resyncing”; newer events wait until replay completes |
| Changes | Diff metadata placeholders | Explain that changes appear after an observed write; link to integration status | Failed diff/artifact fetch is scoped to that row | Verification badge and actor/provenance visible | Missing artifact is explicit; never render an empty proof panel |
| Verification | Command placeholders | Explain why no baseline exists and label verdict inconclusive | Show failed command, exit status, bounded output, and rerun eligibility | Signed proof summary with deterministic checks first | Skipped/pre-existing checks stay distinct from new failures |
| Approval | Current-state fetch inside the action | Not applicable; absent action returns to Inbox | Submission remains unresolved; refetch state before retry | Server-confirmed receipt with actor, target, and resulting state | Expired/stale/revoked actions disable confirmation and explain why |
| Repairs | Candidate/evidence skeletons | Explain eligibility and how to send a supported failure | Preserve reproduction evidence; publication retry never reruns evaluation silently | Draft-PR link, selected candidate, checks, and rollback | Individual candidate/artifact failures remain visible and ranked out |
| Policies/admin | Form/table skeletons | Role-aware explanation and primary setup action | Field-level issue plus request ID for server failure | Saved version and effective timestamp | Managed/inherited values remain visibly locked with source |

All asynchronous controls have idle, pressed/focused, loading, disabled, error, and confirmed
states. Success is silent only when the resulting state is immediately visible; otherwise announce
it through an `aria-live="polite"` region or native accessibility notification.

### Core journey

| Step | User does | Intended feeling | Required design support |
|---|---|---|---|
| 1 | Opens LoopGuard after a notification | Oriented, not alarmed | The exact blocked/regressed item is first, with age, repo, agent, and severity in text |
| 2 | Inspects the run/change | In control | State, proof, and provenance precede raw logs; data freshness is visible |
| 3 | Reviews a proposed action | Cautious confidence | Target, scope, effect, risk, expiry, and expected resulting state use plain language |
| 4 | Approves or rejects | Certain the tap was received once | Biometric/explicit confirmation where required, in-flight lock, server-confirmed receipt |
| 5 | Returns later | Able to audit | Timeline and Audit connect the action to actor, evidence, outcome, and immutable IDs |

The five-second experience answers “what needs me?” The five-minute experience lets the user
verify and act without opening a terminal. The long-term experience builds trust through stable
proof, predictable controls, and complete audit history.

### Accessibility contract

- Web meets WCAG 2.2 AA, including 4.5:1 body-text contrast, 3:1 large-text/control contrast,
  visible focus, skip link, landmarks, logical headings, table captions, error summaries, and no
  colour-only status. Visited documentation/artifact links remain distinguishable.
- iOS supports VoiceOver, Voice Control, Differentiate Without Color, Increase Contrast, Reduce
  Motion, Bold Text, and Dynamic Type through the largest accessibility size without clipped
  primary actions.
- Live regions announce new blocking items and action outcomes but never stream every timeline
  event. Countdown announcements occur at meaningful thresholds, not every second.
- Destructive/high-risk actions cannot rely on swipe gestures, icon-only labels, or colour.
- Screenshot and accessibility baselines cover light, dark, high contrast, reduced motion, a
  375-point phone, iPad split view, 320/768/1280/1536 CSS-pixel web viewports, keyboard-only web,
  and largest Dynamic Type.

### Task 1: Publish and validate the client API contract

**Files:**
- Create: `services/control-api/scripts/export_openapi.py`
- Create: `contracts/control-api.openapi.json`
- Create: `contracts/fixtures/session-stream.jsonl`
- Create: `contracts/fixtures/action-states.json`
- Create: `docs/reference/control-api.md`
- Test: `services/control-api/tests/test_openapi_contract.py`

- [ ] **Step 1: Write failing required-operation tests**

```python
def test_openapi_contains_required_control_operations(app):
    paths = app.openapi()["paths"]
    assert "/v1/sessions" in paths
    assert "/v1/sessions/{session_id}" in paths
    assert "/v1/changes" in paths
    assert "/v1/changes/{change_id}" in paths
    assert "/v1/verifications" in paths
    assert "/v1/verifications/{verification_id}" in paths
    assert "/v1/actions" in paths
    assert "/v1/actions/{action_id}" in paths
    assert "/v1/repairs" in paths
    assert "/v1/repairs/{repair_id}" in paths
    assert "/v1/preferences" in paths
    assert "/v1/devices/pairing/start" in paths
    assert "/v1/devices/pairing/complete" in paths
    assert "/v1/stream-tickets" in paths
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

Generate a versioned human-readable API reference from the same OpenAPI source with authentication,
permissions, idempotency, cursor domain, rate/size limits, request/response examples, error codes,
and webhook/signature examples. Documentation generation is deterministic and CI fails on drift.

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
git add services/control-api contracts docs/reference/control-api.md
git commit -m "feat: publish versioned control api contract"
```

### Task 2: Scaffold the web application and typed client

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/package-lock.json`
- Create: `apps/web/next.config.ts`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/src/app/layout.tsx`
- Create: `apps/web/src/app/page.tsx`
- Create: `apps/web/src/app/api/control/[...path]/route.ts`
- Create: `apps/web/src/styles/tokens.css`
- Create: `apps/web/src/styles/globals.css`
- Create: `apps/web/src/lib/api/generated.ts`
- Create: `apps/web/src/lib/api/server-client.ts`
- Create: `apps/web/src/lib/api/browser-client.ts`
- Create: `apps/web/src/lib/api/client.test.ts`
- Create: `docs/product/design-system.md`

- [ ] **Step 1: Write failing auth and error tests**

```ts
import { BrowserControlClient } from "./browser-client";
import { ServerControlClient } from "./server-client";

test("server proxy sends bearer token and request id upstream", async () => {
  const fetcher = vi.fn().mockResolvedValue(ok({ items: [] }));
  const client = new ServerControlClient({ baseUrl: "https://api.test", token: () => "token", fetcher });
  await client.sessions();
  expect(fetcher.mock.calls[0][1].headers.Authorization).toBe("Bearer token");
  expect(fetcher.mock.calls[0][1].headers["X-Request-ID"]).toBeTruthy();
});

test("browser client uses same-origin BFF and never receives a bearer token", async () => {
  const fetcher = vi.fn().mockResolvedValue(ok({ items: [] }));
  const client = new BrowserControlClient({ fetcher, csrfToken: () => "csrf" });
  await client.sessions();
  expect(fetcher.mock.calls[0][0]).toBe("/api/control/v1/sessions");
  expect(fetcher.mock.calls[0][1].headers.Authorization).toBeUndefined();
});

test("maps stale action response from BFF to typed error", async () => {
  const fetcher = vi.fn().mockResolvedValue(problem(409, "stale_state"));
  await expect(new BrowserControlClient(browserOptions(fetcher)).createAction(action())).rejects
    .toMatchObject({ code: "stale_state" });
});
```

- [ ] **Step 2: Verify test failure**

Run: `cd apps/web && npm test`
Expected: FAIL because the app/client is absent.

- [ ] **Step 3: Generate types and implement the client wrapper**

Use `openapi-typescript` against `contracts/control-api.openapi.json`. A server-only client owns
upstream bearer authentication, request IDs, RFC 9457 problem mapping, cursor parameters, and abort
signals. Browser code calls a same-origin Next.js BFF with an HttpOnly session and CSRF token; it
never reads an access or refresh token. Neither layer adds silent retries to action creation. Set
up App Router, strict TypeScript, ESLint, Vitest, Playwright, and a committed npm lockfile.

Implement the locked design contract before screens: semantic light/dark CSS variables, typography,
spacing, radii, focus, status, motion, and content rules in `docs/product/design-system.md`. Import
only semantic tokens from components. Document why web uses the prototype type families while
native iOS uses system type and why Liquid Glass is limited to native navigation/controls.

- [ ] **Step 4: Run unit tests and typecheck**

Run:

```bash
cd apps/web
npm ci
npm test
npx tsc --noEmit
```

Expected: both commands exit 0.

- [ ] **Step 5: Commit the web foundation**

```bash
git add apps/web docs/product/design-system.md
git commit -m "feat: scaffold typed web control console"
```

### Task 3: Build the web information architecture and live session timeline

**Files:**
- Create: `apps/web/src/app/(console)/layout.tsx`
- Create: `apps/web/src/app/(console)/inbox/page.tsx`
- Create: `apps/web/src/app/(console)/runs/page.tsx`
- Create: `apps/web/src/app/(console)/runs/[id]/page.tsx`
- Create: `apps/web/src/app/(console)/changes/page.tsx`
- Create: `apps/web/src/app/(console)/changes/[id]/page.tsx`
- Create: `apps/web/src/app/(console)/verification/page.tsx`
- Create: `apps/web/src/app/(console)/verification/[id]/page.tsx`
- Create: `apps/web/src/app/(console)/repairs/page.tsx`
- Create: `apps/web/src/components/console-shell.tsx`
- Create: `apps/web/src/components/async-state.tsx`
- Create: `apps/web/src/components/session-timeline.tsx`
- Create: `apps/web/src/app/api/stream-ticket/route.ts`
- Create: `apps/web/src/lib/api/session-stream.ts`
- Test: `apps/web/src/components/session-timeline.test.tsx`
- Test: `apps/web/e2e/session-reconnect.spec.ts`
- Test: `apps/web/e2e/responsive-navigation.spec.ts`

- [ ] **Step 1: Write failing timeline reducer tests**

```ts
test("replay and live duplicate event render once", () => {
  let state = initialTimeline();
  state = reduceTimeline(state, event({ session_seq: 4, client_stream_seq: 9, event_id: "e4" }));
  state = reduceTimeline(state, event({ session_seq: 4, client_stream_seq: 10, event_id: "e4" }));
  expect(state.items).toHaveLength(1);
  expect(state.lastSessionSeq).toBe(4);
  expect(state.lastClientStreamSeq).toBe(10);
});

test("cursor gap requests replay before applying live event", () => {
  const state = reduceTimeline(
    timelineAt({ session_seq: 4, client_stream_seq: 10 }),
    event({ session_seq: 6, client_stream_seq: 11, event_id: "e6" }),
  );
  expect(state.status).toBe("resyncing");
  expect(state.replayAfterSessionSeq).toBe(4);
});
```

- [ ] **Step 2: Verify failure**

Run: `cd apps/web && npm test -- session-timeline.test.tsx`
Expected: FAIL because timeline state is absent.

- [ ] **Step 3: Implement shell, routes, and replay-safe streaming**

Implement the locked wide/tablet/mobile navigation and every route named in it. Navigation:
Inbox, Runs, Changes, Verification, Repairs, Costs, Policies, Devices, Audit. The run detail groups
events into turns and phase boundaries, shows model/effort and cost separately, puts current
state/action and verification proof before the timeline, and keeps raw tool output behind
disclosure. Changes and Verification are real list/detail destinations, not dead navigation labels.
Use the shared async-state contract for loading, empty, scoped error, stale, resyncing, and success
states.

Browser WebSockets cannot receive the upstream bearer token. The browser first requests a
same-origin, CSRF-protected, one-use stream ticket from the BFF. The ticket is bound to user,
tenant, session, allowed cursor scope, and origin, expires within 30 seconds, and is consumed during
the WebSocket upgrade. Never put access tokens in URLs. Reconnect from the last applied
session-stream cursor and fetch gaps before rendering newer live data.

- [ ] **Step 4: Run unit and reconnect tests**

Run:

```bash
cd apps/web
npm test -- session-timeline.test.tsx
npx playwright test e2e/session-reconnect.spec.ts e2e/responsive-navigation.spec.ts
```

Expected: PASS at 320, 768, 1280, and 1536 CSS-pixel widths with keyboard-only navigation,
no hidden primary actions, and no bearer token exposed to browser JavaScript or a WebSocket URL.

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

Before enabling confirmation, fetch the current action challenge and show the exact target, effect,
risk, parameters hash, expected state, and expiry. Web confirmation obtains a registered WebAuthn
assertion through the BFF; iOS confirmation uses its registered device key in Task 8. If state,
content hash, device status, or expiry changes, discard the challenge and require a fresh review.

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
- Create: `apps/ios/LoopGuard/DesignSystem/SemanticColor.swift`
- Create: `apps/ios/LoopGuard/DesignSystem/Typography.swift`
- Create: `apps/ios/LoopGuard/DesignSystem/ControlStyle.swift`
- Create: `apps/ios/LoopGuard/DesignSystem/AsyncStateView.swift`
- Create: `apps/ios/LoopGuard/Security/KeychainStore.swift`
- Create: `apps/ios/LoopGuardTests/ControlAPITests.swift`
- Create: `apps/ios/LoopGuardTests/DesignSystemTests.swift`
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

Implement semantic light/dark colours, native Dynamic Type styles, SF Symbols, reusable async
states, status labels, focus treatment, and action styles from `docs/product/design-system.md`.
Use system typography rather than porting the Expo fonts into native screens. Liquid Glass is
available only through shared navigation/control modifiers with non-glass fallbacks; no feature
view invents its own material, colour, radius, or animation.

- [ ] **Step 4: Run iOS unit tests**

Run through XcodeBuildMCP with the generated project, `LoopGuard` scheme, and an iOS 26 simulator.
Expected: all unit tests PASS.

- [ ] **Step 5: Commit native iOS foundation**

```bash
git add apps/ios
git commit -m "feat: scaffold native loopguard ios app"
```

### Task 6: Implement OIDC sign-in and cryptographic device pairing

**Files:**
- Create: `apps/web/src/auth.ts`
- Create: `apps/web/src/middleware.ts`
- Create: `apps/web/src/app/auth/callback/route.ts`
- Create: `apps/web/src/app/(public)/sign-in/page.tsx`
- Create: `apps/web/src/security/webauthn.ts`
- Test: `apps/web/src/auth.test.ts`
- Test: `apps/web/src/security/webauthn.test.ts`
- Create: `apps/ios/LoopGuard/Features/Authentication/SignInView.swift`
- Create: `apps/ios/LoopGuard/Features/Authentication/AuthSession.swift`
- Create: `apps/ios/LoopGuard/Features/Devices/DevicePairingView.swift`
- Create: `apps/ios/LoopGuard/Security/DeviceKeyStore.swift`
- Modify: `apps/ios/LoopGuard/App/AppModel.swift`
- Test: `apps/ios/LoopGuardTests/AuthSessionTests.swift`
- Test: `apps/ios/LoopGuardTests/DevicePairingTests.swift`

- [ ] **Step 1: Write failing web and iOS trust-chain tests**

Web:

```ts
test("callback rejects an OIDC state mismatch", async () => {
  const result = await completeCallback(callback({ state: "substituted" }), session({ state: "expected" }));
  expect(result).toMatchObject({ ok: false, code: "invalid_state" });
});

test("protected route redirects an unauthenticated request", async () => {
  expect(await routeFor(request("/runs/active"))).toMatchObject({
    status: 307,
    location: "/sign-in",
  });
});

test("web action proof is bound to the server action challenge", async () => {
  const assertion = await signActionChallenge(actionChallenge(), registeredPasskey());
  expect(verifyFixtureAssertion(assertion)).toMatchObject({
    userVerified: true,
    origin: "https://loopguard.test",
    rpId: "loopguard.test",
  });
});
```

iOS tests must prove:

- PKCE uses a fresh high-entropy verifier and S256 challenge for each authorization.
- Callback state and ID-token nonce mismatches are rejected.
- Access and refresh tokens are stored only in Keychain with device-only accessibility.
- A device P-256 Secure Enclave key is generated when available. On devices without that
  capability, an Ed25519 software key is generated, encrypted with a device-only Keychain key,
  marked non-synchronizable, and never exposed through application APIs; tests do not falsely
  claim this fallback is Secure Enclave-backed or cryptographically non-exportable.
- Only the signing algorithm and public key are registered, and a pairing challenge cannot be
  completed twice.
- Revocation removes local credentials and prevents another device-signed action.

- [ ] **Step 2: Run focused tests and verify trust is absent**

Run:

```bash
cd apps/web
npm test -- auth.test.ts
```

Run `AuthSessionTests` and `DevicePairingTests` through XcodeBuildMCP.
Expected: FAIL because browser login, mobile login, and device proof-of-possession are absent.

- [ ] **Step 3: Implement OIDC Authorization Code + PKCE and pairing**

The web app uses server-side OIDC Authorization Code + PKCE with strict issuer, audience, signature,
expiry, state, and nonce validation. Keep refresh/access tokens in encrypted, Secure, HttpOnly,
SameSite cookies; protect console routes in middleware; rotate the session after callback; and add
CSRF protection to state-changing browser requests. Never expose an OIDC client secret to browser
JavaScript. Register a WebAuthn credential for web-device action proof. Require verified origin,
RP ID, challenge, credential/user binding, sign counter/backup-state policy, and user verification;
store only the public credential server-side. A browser approval signs the exact server-issued
canonical action challenge before the BFF submits it.

The iOS app uses `ASWebAuthenticationSession`, an ephemeral PKCE verifier, strict callback/nonce
validation, and Keychain-backed credentials. Generate a P-256 Secure Enclave signing key when
available; otherwise generate an Ed25519 software key encrypted by a device-only,
non-synchronizable Keychain key and make the limitation visible in capability metadata. Call
`/v1/devices/pairing/start`, sign the returned one-time challenge, then call
`/v1/devices/pairing/complete` with only the allowlisted algorithm, public key, and signature. Key
material never crosses the network or enters logs. Logout and remote revocation clear tokens and
local device-key material. No embedded client secret is permitted in the app.

- [ ] **Step 4: Run authentication, pairing, and regression tests**

Run:

```bash
cd apps/web
npm test -- auth.test.ts
npx tsc --noEmit
```

Run all iOS unit tests through XcodeBuildMCP, then rerun the control API authentication and device
pairing tests from the cloud-control-plane plan.
Expected: PASS, including replay, state mismatch, nonce mismatch, logout, and revocation cases.

- [ ] **Step 5: Commit the client trust chain**

```bash
git add apps/web apps/ios
git commit -m "feat: add client authentication and device pairing"
```

### Task 7: Build the complete iOS monitoring and navigation surface

**Files:**
- Create: `apps/ios/LoopGuard/Features/Inbox/InboxView.swift`
- Create: `apps/ios/LoopGuard/Features/Inbox/InboxViewModel.swift`
- Create: `apps/ios/LoopGuard/Features/Runs/RunListView.swift`
- Create: `apps/ios/LoopGuard/Features/Runs/RunDetailView.swift`
- Create: `apps/ios/LoopGuard/Features/Runs/TimelineReducer.swift`
- Create: `apps/ios/LoopGuard/Features/Changes/ChangeListView.swift`
- Create: `apps/ios/LoopGuard/Features/Changes/ChangeDetailView.swift`
- Create: `apps/ios/LoopGuard/Features/Repairs/RepairListView.swift`
- Create: `apps/ios/LoopGuard/Features/Repairs/RepairDetailView.swift`
- Create: `apps/ios/LoopGuard/Features/Settings/SettingsView.swift`
- Create: `apps/ios/LoopGuard/Features/Settings/DeviceListView.swift`
- Create: `apps/ios/LoopGuardTests/TimelineReducerTests.swift`
- Create: `apps/ios/LoopGuardTests/InboxOrderingTests.swift`
- Create: `apps/ios/LoopGuardUITests/InboxUITests.swift`
- Create: `apps/ios/LoopGuardUITests/NavigationUITests.swift`

- [ ] **Step 1: Write failing timeline and priority tests**

```swift
@Test func duplicateSessionSequenceIsIgnored() {
    var state = TimelineState()
    state.apply(.fixture(sessionSeq: 3, clientStreamSeq: 7, id: "e3"))
    state.apply(.fixture(sessionSeq: 3, clientStreamSeq: 8, id: "e3"))
    #expect(state.items.count == 1)
    #expect(state.lastSessionSeq == 3)
    #expect(state.lastClientStreamSeq == 8)
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

Use a `TabView` with implemented destinations for Inbox, Runs, Changes, Repairs, and Settings. The
Inbox shows only items that need attention plus quiet completion summaries. Run detail shows
current state/action, verification proof, phase, agent, model/effort, cost, and then a compact
timeline. Change detail shows actor/provenance, diff, and linked proof. Repair detail shows
reproduction evidence, candidates, deterministic ranking, checks, rollback, and draft-PR state.
Settings owns account, paired devices, notifications, privacy/local-only controls, and app version.

Apply Liquid Glass only to tab/navigation/transient control surfaces; use standard grouped content
surfaces rather than generic cards. Implement every loading, empty, scoped-error, success, stale,
offline, and resyncing state from the locked contract. Support Dynamic Type, VoiceOver, Voice
Control, Differentiate Without Color, reduced motion, high contrast, iPad split view, pointer
states, and hardware-keyboard navigation.

- [ ] **Step 4: Run unit/UI tests and inspect simulator screenshots**

Run all iOS tests through XcodeBuildMCP, launch in the simulator, and capture Inbox, Run Detail,
Change Detail, Repair Detail, and Settings in light/dark mode, largest Dynamic Type, and iPad split
view. Exercise offline/stale, empty, loading, and error fixtures in UI tests.
Expected: tests PASS with no dead tabs, clipped controls, colour-only state, missing accessibility
labels, or unreadable contrast.

- [ ] **Step 5: Commit iOS monitoring surfaces**

```bash
git add apps/ios
git commit -m "feat: add ios safety inbox and run timeline"
```

### Task 8: Implement iOS actions, notifications, and Live Activities

**Files:**
- Modify: `apps/ios/project.yml`
- Create: `apps/ios/LoopGuard/LoopGuard.entitlements`
- Create: `apps/ios/LoopGuard/Features/Actions/ActionReviewView.swift`
- Create: `apps/ios/LoopGuard/Features/Actions/ActionViewModel.swift`
- Create: `apps/ios/LoopGuard/Notifications/NotificationManager.swift`
- Create: `apps/ios/LoopGuardShared/Activities/RunActivityAttributes.swift`
- Create: `apps/ios/LoopGuardWidgets/LoopGuardWidgets.swift`
- Create: `apps/ios/LoopGuardWidgets/RunLiveActivity.swift`
- Create: `apps/ios/LoopGuardWidgets/LoopGuardWidgets.entitlements`
- Create: `apps/ios/LoopGuardTests/ActionViewModelTests.swift`
- Create: `apps/ios/LoopGuardTests/ActionSigningTests.swift`
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
confirmation for organization-configured high-risk actions. The device signs the canonical action
request, including action ID, target, expected state/version, nonce, and expiry, before the server
countersigns it; stale or changed content requires a new review. APNs payloads carry only action or
session identifiers and generic text; fetch sensitive detail after authentication.

Create a real WidgetKit extension target with shared `ActivityAttributes`, App Group/keychain
sharing only where required, extension-specific entitlements, preview fixtures, and deep-link
tests. The Live Activity shows only non-sensitive phase/progress, never source, prompt, path,
repository, or action parameters. It deep-links to authenticated run detail and ends when the run
finishes, is revoked, or exceeds its bounded lifetime.

- [ ] **Step 4: Run unit/UI tests and push-notification fixture tests**

Run all iOS tests through XcodeBuildMCP, deliver local notification fixtures in Simulator, and
build/preview the WidgetKit extension.
Expected: PASS; expired/deep-linked actions fetch current state before enabling controls,
device signatures cover the exact reviewed payload, and extension previews contain no sensitive
fixture data.

- [ ] **Step 5: Commit iOS action flows**

```bash
git add apps/ios
git commit -m "feat: add secure ios approvals and notifications"
```

### Task 9: Add policies, costs, devices, and audit to web

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

### Task 10: Establish visual, accessibility, and migration gates

**Files:**
- Create: `apps/web/e2e/accessibility.spec.ts`
- Create: `apps/web/e2e/design-system.spec.ts`
- Create: `apps/web/e2e/visual.spec.ts`
- Create: `apps/web/e2e/__screenshots__/.gitkeep`
- Create: `apps/ios/LoopGuardUITests/AccessibilityUITests.swift`
- Create: `apps/ios/LoopGuardUITests/VisualStateUITests.swift`
- Create: `docs/product/control-surfaces.md`
- Modify: `cloud-app/README.md`

- [ ] **Step 1: Add failing accessibility assertions**

Web:

```ts
test("inbox has no axe violations against the configured WCAG 2.2 AA rules", async ({ page }) => {
  await page.goto("/inbox");
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
```

iOS UI tests must assert every actionable element has a non-empty accessibility label and the
primary flows remain hittable at the largest supported content size. Add keyboard-only web tests,
skip-link/landmark/heading checks, meaningful live-region assertions, high-contrast/reduced-motion
fixtures, and tests that status remains understandable with colour removed.

- [ ] **Step 2: Run web and iOS accessibility tests**

Expected: FAIL until violations and labels are corrected.

- [ ] **Step 3: Fix all violations and document migration**

Capture stable visual references for Inbox, Run Detail, Action Review, Changes, Verification,
Repair, Policies, and Settings in light/dark modes. Cover 320/768/1280/1536 CSS-pixel web widths,
375-point iPhone, iPad split view, largest Dynamic Type, high contrast, loading, empty, error,
offline/stale, and success fixtures.

Add a deterministic source/design check that rejects raw colours outside token files, placeholder-
only labels, emoji controls, decorative gradients/blobs, generic three-column feature grids,
content glass, indiscriminate card wrappers, and action/status conveyed only by colour. Document
intentional exceptions. Document how features move from `cloud-app`, mark the Expo app deprecated
only after route/action/state parity, and retain it for one release as a fallback. Do not delete it
in this plan.

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

### Task 11: Package reproducible web and iOS client releases

**Files:**
- Create: `apps/web/Dockerfile`
- Create: `apps/web/src/app/health/route.ts`
- Create: `apps/ios/LoopGuard/Resources/PrivacyInfo.xcprivacy`
- Create: `apps/ios/ExportOptions.plist`
- Create: `.github/workflows/client-release.yml`
- Create: `scripts/verify_client_release.sh`
- Create: `docs/operations/client-release.md`
- Test: `apps/web/src/app/health/route.test.ts`

- [ ] **Step 1: Write failing release-artifact checks**

```ts
import { GET } from "./route";

test("web health response identifies the immutable build", async () => {
  process.env.BUILD_SHA = "abc123";
  const response = await GET();
  expect(await response.json()).toEqual({ status: "ok", build_sha: "abc123" });
});
```

`scripts/verify_client_release.sh` must initially fail unless:

- The web image reference contains an immutable digest.
- The iOS archive has the expected bundle identifier and version.
- `PrivacyInfo.xcprivacy` is present in the archive.
- Export settings use App Store distribution and do not contain signing secrets.
- Web and iOS artifact checksums are present.

- [ ] **Step 2: Run release checks before packaging exists**

Run:

```bash
cd apps/web
npm test -- health/route.test.ts
npm run build
cd ../..
scripts/verify_client_release.sh
```

Expected: FAIL because the health route, release container, iOS privacy manifest, archive, and
checksums are absent.

- [ ] **Step 3: Implement reproducible client packaging**

The web Dockerfile uses a pinned Node base image, installs with `npm ci`, builds Next.js standalone
output, copies only runtime artifacts into a non-root read-only image, and exposes the health
route. The image embeds `BUILD_SHA` and is published by digest.

The iOS privacy manifest declares accessed API categories and collected-data behavior matching the
implemented app. `ExportOptions.plist` uses App Store Connect distribution without embedding
credentials. `client-release.yml`:

- Builds/tests web and iOS before packaging.
- Produces checksums, SBOM/provenance for web, and an unsigned verification archive on ordinary PRs.
- Uses environment-protected App Store Connect credentials only in an explicitly approved release
  job.
- Uploads to TestFlight but never submits for App Review automatically.
- Publishes the web image by signed digest but does not deploy it.

- [ ] **Step 4: Verify packaging without publishing**

Run:

```bash
cd apps/web
npm ci
npm test
npx tsc --noEmit
npm run build
docker build --build-arg BUILD_SHA=test-sha -t loopguard-web:test .
cd ../ios
xcodegen generate
xcodebuild archive -scheme LoopGuard -archivePath build/LoopGuard.xcarchive \
  -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO
cd ../..
scripts/verify_client_release.sh
```

Expected: all local build and verification commands exit 0. The workflow is syntax-validated but
no image, TestFlight build, or App Store submission is published.

- [ ] **Step 5: Commit client release packaging**

```bash
git add apps/web apps/ios .github/workflows/client-release.yml \
  scripts/verify_client_release.sh docs/operations/client-release.md
git commit -m "ops: package web and ios client releases"
```

## Completion gate

Web:

```bash
cd apps/web
npm ci
npm test
npx tsc --noEmit
npx playwright test
npm run build
```

iOS: build and run all unit/UI tests with XcodeBuildMCP on an iOS 26 simulator, inspect light/dark
and accessibility-size screenshots, then produce the unsigned verification archive.

Expected: all checks pass; actions are replay-safe; sensitive content is absent from push payloads;
OIDC callbacks reject substituted state/nonce; paired devices prove possession of non-exported
private keys; content surfaces use native hierarchy rather than indiscriminate glass styling; and
reproducible client packages pass local verification without publishing.
