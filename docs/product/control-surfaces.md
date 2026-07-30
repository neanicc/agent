# LoopGuard control surfaces

LoopGuard has two production clients over the same versioned control API:

- The web console is the broad operational workbench for runs, changes, verification, hosts,
  policies, cost, devices, and immutable audit history.
- The native SwiftUI app is the focused safety client for attention, proof-first run inspection,
  device security, notifications, and signed action review.

Neither client invents capability. Navigation and controls come from authenticated server state,
and unknown, stale, unavailable, or revoked inputs fail closed.

## Information architecture

| Need | Web | iOS |
|---|---|---|
| Prioritized attention | Inbox | Inbox |
| Run state, proof, and timeline | Runs | Runs |
| Change provenance and diff | Changes | Changes |
| Deterministic proof inventory | Verification | Run/change detail |
| Host and adapter health | Hosts & integrations | Settings → Hosts & integrations |
| Safety policy | Policies | Read-only effects in reviewed actions |
| Observed provider cost | Costs | Run detail |
| Signing-device lifecycle | Devices | Settings → Paired devices |
| Immutable control history | Audit | Authoritative receipts in context |
| Explicit action review | Run detail | Inbox/action deep link |

Repairs stay absent until the server reports the completed Auto-Heal capability as ready. Empty
repair data is not treated as availability.

## Safety interaction contract

An action review shows the exact target, effect, risk, parameter hash, expected state, and expiry.
The client fetches current authenticated action detail before enabling confirmation. Web requires a
registered, user-verified WebAuthn assertion; iOS signs with its paired device key and additionally
requires device-owner authentication for server-designated high risk.

Submission progresses through reviewed, signed, queued, delivered, host executing, reconciling,
and a server-authoritative terminal state. HTTP acceptance is only queued work. Execution is shown
only with a daemon-derived receipt. Expiry, changed content, host loss, revocation, duplicate
submission, and acknowledgement ambiguity never trigger an automatic re-sign or resubmit.

## State and accessibility contract

Loading, empty, scoped error, stale/offline, resyncing, success, and unavailable states use explicit
text. Status always includes a label and symbol; colour is supplementary. Errors identify a retry
path and preserve a request ID when available. Live changes such as save completion, copy outcome,
stream state, and action submission use polite announcements.

The web console meets these enforced rules:

- One main landmark, named navigation, ordered page headings, and a skip link that transfers focus.
- Full keyboard operation, visible focus, 44 CSS-pixel touch controls on coarse pointers, and no
  horizontal document overflow at 320, 768, 1280, or 1536 CSS pixels.
- Axe checks configured for WCAG 2.0/2.1 A and AA plus WCAG 2.2 AA on every core route.
- Light, dark, forced-colour/high-contrast, reduced-motion, and colour-removed verification.

The native app uses system controls, semantic colours, SF Symbols, Dynamic Type, VoiceOver names,
and a minimum 44-point touch target. The UI suite exercises the largest accessibility content size,
light/dark and increased-contrast/reduced-motion fixtures, explicit async states, and both iPhone
and iPad layouts.

## Visual system

The product uses a technical, austere workbench rather than a dashboard of cards. Space, rules,
type, and one cobalt accent establish hierarchy. IBM Plex Sans carries interface text, Space
Grotesk carries headings, and IBM Plex Mono carries identifiers and telemetry. Content surfaces are
opaque; native Liquid Glass is reserved for navigation and transient controls.

The deterministic source gate rejects raw colours outside token files, decorative gradients and
blur, generic three-column feature grids, generic card/glass wrappers, emoji controls, and
placeholder-only field naming. The lifecycle line and responsive fact grids are intentional data
structures, not marketing feature grids. Native system semantic colours are an intentional
platform exception because they adapt to accessibility and appearance settings.

## Visual references and verification

Playwright baselines live beside `apps/web/e2e/visual.spec.ts`. They cover Inbox, Run Detail, Action
Review, Changes, Verification, Hosts & integrations, Policies, and Devices/Settings in light and
dark appearance, plus 320/768/1536 responsive references and forced-colour reduced motion.

Run the web gate:

```bash
cd apps/web
npm test
npm run typecheck
npm run lint
npx playwright test
```

Run the native gate with the `LoopGuard` scheme through XcodeBuildMCP, or:

```bash
cd apps/ios
xcodegen generate
xcodebuild test \
  -project LoopGuard.xcodeproj \
  -scheme LoopGuard \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

Baseline changes require an intentional UI change, visual inspection in both appearances, and
`npx playwright test e2e/visual.spec.ts --update-snapshots`.

## Migration from `cloud-app`

`cloud-app` remains a compatibility reference while native parity is completed. New production
work belongs in `apps/web`, `apps/ios`, and the versioned `services/control-api` contract.

The migration is incremental:

1. Web owns authenticated operational and administration routes.
2. Native iOS owns the safety inbox, monitoring, settings, notifications, and signed reviews.
3. Auto-Heal adds real repair routes and actions only after its backend capability is ready.
4. Control Surface Task 12 proves final route, action, state, accessibility, and visual parity.
5. Only after that proof may `cloud-app` receive a deprecation notice and removal schedule.

Until step 4 is green, do not delete `cloud-app`, redirect its users silently, or describe it as
deprecated. It must not receive new production architecture, security, or workflow behavior.
