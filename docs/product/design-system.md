# LoopGuard Product Design System

This is the production design contract for the native iOS app and web control console. It carries
the operational vocabulary of [`cloud-app/design.md`](../../cloud-app/design.md) forward without
redefining or copying the Expo implementation.

## Product posture

- **Audience:** developers and operators who understand repositories, agents, verification, and
  change review. Plain language comes first; exact technical evidence remains available.
- **Primary job:** identify what needs attention, inspect the proof, and take one safe action.
- **Tone:** technical, austere, calm, and direct. The product reports state; it does not perform
  urgency through decoration.
- **Macrostructure:** operational Workbench. State-led sections, compact headers, separated rows,
  and live evidence are the visual anchor.
- **Truth rule:** never invent cost savings, success rates, urgency, health, or completion. Unknown,
  stale, skipped, pre-existing, and inconclusive are real states with their own language.

## Shared visual principles

1. Cool neutral surfaces carry nearly all area. Cobalt marks selection, navigation, links, and
   focus; it is not a large decorative fill.
2. Green, amber, and red are semantic only. Every semantic colour is paired with text and an icon,
   never used as the sole signal.
3. Rows, rails, tables, timelines, and disclosure groups are preferred over dashboard-card mosaics.
   A card exists only for a bounded interactive object such as an approval or repair candidate.
4. Hierarchy comes from weight, scale, space, and rules. Decorative shadows, gradients, glows,
   ambient blobs, coloured icon circles, fake device/browser chrome, and emoji controls are banned.
5. Actions use direct verbs: “Approve correction”, “Reject request”, “Copy setup command”,
   “Revoke device”. “OK”, “Submit”, “Click here”, and celebration copy are banned.
6. Success is silent when the resulting state is visible. Otherwise, web uses a polite live region
   and iOS posts a bounded accessibility announcement.

## Semantic colour tokens

Only token files contain raw colour values. Components consume the semantic names below. The web
implementation is [`apps/web/src/styles/tokens.css`](../../apps/web/src/styles/tokens.css); native
colour assets and Swift accessors map the same roles to platform-adaptive values.

| Token | Role | Constraint |
|---|---|---|
| `color-paper` | App canvas | Cool tinted near-white or midnight; never pure white/black |
| `color-paper-2` | Navigation rail and grouped sections | One elevation step from canvas |
| `color-paper-3` | Selected row and bounded field surface | Must preserve body contrast |
| `color-raised` | Popover, sheet, bounded approval | Used sparingly; no decorative shadow |
| `color-rule` | Dividers and ordinary boundaries | Visible without becoming a grid texture |
| `color-rule-strong` | Input boundary and structural separator | At least 3:1 where it defines a control |
| `color-ink` | Primary content | At least 4.5:1 for body text |
| `color-ink-2` | Secondary content | At least 4.5:1 at body size |
| `color-muted` | Metadata and helper content | Never used for essential instructions below 4.5:1 |
| `color-accent` | Navigation, selection, links | Under roughly 3% of a viewport |
| `color-accent-ink` | Text on the rare accent fill | At least 4.5:1 against accent |
| `color-focus` | Keyboard/platform focus | At least 3:1 against both control and canvas |
| `color-success` | Passed/executed/current | Always paired with label and icon |
| `color-warning` | Stale/partial/expiring | Always paired with label and icon |
| `color-danger` | Blocked/failed/revoked/destructive | Never a full panel background |

Light and dark modes keep hue and semantic meaning stable. Dark elevation becomes lighter, not
shadowed. High Contrast may strengthen boundaries and text but must not reorder content or remove
status labels. Differentiate Without Color adds or strengthens icons and textual state.

## Typography

### Web

- **Display:** Space Grotesk 600/700, upright, `-0.025em` tracking. Page titles are compact rather
  than marketing-sized.
- **Interface:** IBM Plex Sans 400/500/600, 16 CSS px minimum for prose. Compact metadata may use
  14 CSS px but not for instructions or primary actions.
- **Telemetry:** IBM Plex Mono 400/500 for identifiers, paths, hashes, commands, costs, event
  syntax, and aligned numbers only. Mono never becomes the general interface font.
- Numbers in tables, cost, duration, sequence, and timestamps use tabular figures.

### Native iOS

Use system semantic text styles and SF Symbols. Do not port the Expo fonts into SwiftUI: Dynamic
Type, Bold Text, language fallback, platform metrics, and VoiceOver pronunciation take precedence
over cross-platform typographic sameness. Use monospaced digits only for aligned numeric evidence;
use the system monospaced face only for code, paths, hashes, or commands.

Both platforms preserve semantic heading order and support unbroken repository/model/path values
without clipping the primary action. Visual truncation always retains the full accessibility label
and a copy action.

## Spacing, geometry, and density

- The base grid is 4 points/CSS pixels. Named steps are 2, 4, 8, 12, 16, 24, 40, 64, and 96.
- Web controls are 44 CSS px on coarse pointers. Fine-pointer operational rows may be 32 CSS px if
  keyboard focus remains visible and every action retains a 44 CSS px hit area when touch is present.
- Native controls and hit regions are at least 44 × 44 points.
- Control radius is 6 CSS px on web. Bounded surfaces use 10 CSS px. Native geometry follows the
  system control shape; content views do not invent per-feature radii.
- One containment layer only. Never place bordered cards inside a bordered card.
- Information density may increase on a run timeline or audit table, but proof and current action
  stay ahead of raw telemetry.

## Status grammar

| Meaning | Label examples | Symbol family | Colour |
|---|---|---|---|
| Current/success | Ready, Passed, Executed, Current | checkmark/circle-check | success |
| Attention | Partial, Stale, Expiring, Inconclusive | warning triangle/clock | warning |
| Blocking/failure | Blocked, Failed, Revoked, Offline | xmark/octagon-x | danger |
| Active/transport | Replaying, Queued, Delivered, Executing | arrow/clock/antenna | accent or ink |
| Neutral | Completed, Skipped, Pre-existing, Unknown | circle/info/minus | ink-2 |

Status copy names the observed condition before offering a remedy. “Daemon health is stale. Run
`loopguard doctor` on the host.” is acceptable. “Something went wrong” is not.

## Icons

- Web uses one outlined icon family with consistent 1.75–2 CSS px optical stroke. iOS uses the
  closest semantic SF Symbol. Do not mix icon libraries or use emoji.
- Icon-only controls require a stable accessible name and visible tooltip on hover after 800 ms or
  immediately on keyboard focus. Destructive/high-risk actions are never icon-only.
- Coloured icon tiles and decorative icon circles are banned. A status icon may inherit its
  semantic colour beside the matching text.

## Components and states

Shared component families are: app shell, destination link, page header, status label, priority
row, evidence row, timeline event, disclosure group, async-state region, command copy control,
button/action control, form field, approval sheet, table/labelled mobile row, banner, toast/error
receipt, and empty state.

Every asynchronous or interactive control implements:

1. idle/default;
2. hover or pointer state where the platform supports it;
3. visible keyboard/platform focus;
4. pressed/active feedback;
5. disabled with a textual reason where it matters;
6. loading/in-flight with duplicate submission locked;
7. error with an actionable message and request ID for server failures;
8. confirmed/success, only after authoritative state is known.

Focus appears instantly. Border thickness never changes between field states. Loading replaces or
updates the initiating control without flashing for very short waits. A destructive irreversible
action requires explicit review; a server-accepted action is not rendered as successful until the
daemon resolves it.

## Async content states

- **Loading:** preserve the geometry with bounded skeletons; use an inline progress indicator only
  for the initiating control.
- **Empty:** name what is absent, explain why it matters, then offer one real next action.
- **Error:** retain the last valid data when safe, label it stale, and give one retry plus a request
  ID/diagnostics disclosure.
- **Success:** show the resulting state or immutable receipt. Do not add a celebratory toast.
- **Partial/stale:** preserve available evidence, name missing fields/artifacts explicitly, show
  freshness, and disable destructive controls.
- **Replay gap:** label the timeline “Resyncing”; fetch durable gaps before applying newer events.

## Navigation and responsive behavior

Navigation is generated from effective capabilities. An unavailable destination is absent—not a
disabled promise. Repairs stays absent until the server reports a fresh `repair.ready` capability.

- **Web ≥1280 CSS px:** persistent labelled sidebar and two-pane list/detail where useful.
- **Web 768–1279 CSS px:** compact icon rail with accessible labels/tooltips; one pane plus a
  dismissible inspector.
- **Web <768 CSS px:** top app bar and full-height navigation sheet; one-column content and safe-area
  action footer. Tables become labelled row groups without dropping fields.
- Verify 320, 375, 414, 768, 1280, and 1536 CSS px. `html` and `body` use `overflow-x: clip`.
  Clickable labels remain one line and parent layouts reflow instead.
- **iPhone:** capability-derived native `TabView` with one `NavigationStack` per tab and primary
  action above the safe area.
- **iPad:** sidebar-capable split view, pointer states, commands, and logical hardware-keyboard order.

## Motion

Motion is limited to direct press feedback and state crossfades. Animate transform and opacity
only, using the named exponential easing tokens. Focus, error appearance, and keyboard navigation
are instant. Reduced Motion removes spatial movement and caps opacity transitions at 150 ms.
There is no ambient, looping, bouncing, parallax, universal reveal, or layout-property animation.

## Liquid Glass boundary

On iOS 26, native tab/navigation and transient controls may use Apple’s Liquid Glass APIs. Grouped
content, evidence, timelines, approval content, and repair candidates remain on standard system
surfaces. Multiple adjacent glass controls share a `GlassEffectContainer`; only interactive
controls use interactive glass; modifiers follow layout/appearance; earlier iOS versions receive
native material/control fallbacks. No feature view invents custom blur or glass styling.

This intentionally differs from the Expo prototype, where glass remains prohibited, and from web,
where glassmorphism is prohibited entirely.

## Accessibility release contract

- Web targets WCAG 2.2 AA: body 4.5:1, large text and control boundaries 3:1, landmarks, skip link,
  logical headings, table captions, error summaries, distinguishable visited links, and visible focus.
- iOS supports VoiceOver, Voice Control, Differentiate Without Color, Increase Contrast, Reduce
  Motion, Bold Text, and the largest accessibility Dynamic Type size without losing a primary action.
- Live regions announce newly blocking items and terminal action outcomes, not every stream event.
  Countdown announcements occur only at meaningful thresholds.
- Keyboard/VoiceOver order follows visual and risk order. Raw logs never precede current state,
  proof, or action.

## Content examples

| Need | Use | Avoid |
|---|---|---|
| Empty inbox | “No action needed.” | “You’re all caught up!” |
| Copy command | “Copy setup command” → “Copied” | A generic “Done” toast |
| Stale host | “Daemon health is stale. Run `loopguard doctor` on the host.” | “Host problem” |
| Failed request | “Action state could not be confirmed. Fetch the current action before retrying. Request `…`.” | “Oops, try again” |
| Approval | “Approve correction” | “Confirm” or “OK” |
| Revocation | “Revoke device” | An icon-only trash action |

## Source ownership

`cloud-app/design.md` remains the locked Expo prototype system. This document owns production web
and native divergence. Web token values live only in its token stylesheet. Swift feature views use
shared design-system accessors. Changes to status meaning, action language, navigation capability
rules, or safety hierarchy require corresponding contract and accessibility tests.
