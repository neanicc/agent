# LoopGuard Phone UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Subagent dispatch is excluded by the current collaboration constraints.

**Goal:** Rebuild the Expo app’s visual system and every phone screen as a sober, native-feeling operational console without changing application behavior.

**Architecture:** Keep the existing screen state machine and API client. Centralize colours, typography, spacing, shape, and interaction styles in `src/theme.ts`; rebuild shared primitives in `src/components/ui.tsx`; then migrate every screen to those primitives. Use `expo-symbols` on iOS and a single Feather fallback family elsewhere.

**Tech Stack:** Expo 51, React Native 0.74, TypeScript, expo-font, expo-symbols, Google Font packages, Jest.

---

### Task 1: Lock the design system

**Files:**
- Create: `cloud-app/design.md`
- Create: `cloud-app/tokens.css`
- Create: `cloud-app/.hallmark/preflight.json`
- Create: `cloud-app/.hallmark/log.json`
- Modify: `cloud-app/package.json`
- Modify: `cloud-app/package-lock.json`
- Modify: `cloud-app/app.json`
- Modify: `cloud-app/App.tsx`
- Modify: `cloud-app/src/theme.ts`

- [ ] Install `expo-symbols`, Space Grotesk, IBM Plex Sans, and IBM Plex Mono with Expo-compatible versions.
- [ ] Record the approved Workbench/Midnight system in `design.md` and the portable OKLCH tokens in `tokens.css`.
- [ ] Replace the current multi-accent theme with opaque named tokens and native hex equivalents.
- [ ] Load the three approved font roles before rendering the application.
- [ ] Make the root status bar and boot surface match the new paper token.
- [ ] Record the Hallmark preflight and app-wide redesign entry.

Run:

```bash
cd cloud-app
npx tsc --noEmit
```

Expected: exit code 0.

### Task 2: Rebuild shared native primitives

**Files:**
- Create: `cloud-app/src/components/AppIcon.tsx`
- Modify: `cloud-app/src/components/ui.tsx`
- Modify: `cloud-app/src/components/TabBar.tsx`
- Modify: `cloud-app/src/components/Meter.tsx`
- Modify: `cloud-app/src/components/EventRow.tsx`
- Modify: `cloud-app/src/components/DecisionCard.tsx`

- [ ] Add one icon adapter: SF Symbols on iOS, Feather glyphs on web and Android.
- [ ] Replace `AmbientBackground`, `Hero`, decorative pills, and glass `Card` with `Screen`, `PageHeader`, `Section`, `StatusBadge`, `Field`, and `ActionButton`.
- [ ] Implement 48 point controls, pressed-state feedback, focusable accessibility roles, loading labels, and semantic error/success variants.
- [ ] Replace the nested pill tab bar with a flat safe-area-aware bar and short single-line labels.
- [ ] Rebuild telemetry, events, and intervention actions with tabular values and no side stripes or emoji.

Run:

```bash
cd cloud-app
npx tsc --noEmit
```

Expected: exit code 0.

### Task 3: Redesign connection and launch surfaces

**Files:**
- Modify: `cloud-app/src/screens/ConnectScreen.tsx`
- Modify: `cloud-app/src/screens/DashboardScreen.tsx`

- [ ] Convert Connect to a compact service connection form with a visible label and stable helper/error slot.
- [ ] Remove marketing chips, decorative shield imagery, and self-congratulatory copy.
- [ ] Convert active runs and projects into section rows with explicit selected and pending states.
- [ ] Keep mode selection, custom task input, launch behavior, and error handling unchanged.

Run:

```bash
cd cloud-app
npx tsc --noEmit
```

Expected: exit code 0.

### Task 4: Redesign live Mission Control

**Files:**
- Modify: `cloud-app/src/screens/MissionControlScreen.tsx`

- [ ] Build the compact navigation/status header.
- [ ] Place aligned tokens, run cost, and judge cost directly below it.
- [ ] Keep event order and WebSocket behavior unchanged while improving row hierarchy.
- [ ] Keep pending intervention controls visible, readable, and safe at narrow phone widths.
- [ ] Replace all-caps result labels and decorative auto-fix stripes with semantic sections.

Run:

```bash
cd cloud-app
npx tsc --noEmit
```

Expected: exit code 0.

### Task 5: Redesign history, fixes, and allowlist

**Files:**
- Modify: `cloud-app/src/screens/HistoryScreen.tsx`
- Modify: `cloud-app/src/screens/AutoFixScreen.tsx`
- Modify: `cloud-app/src/screens/AllowlistScreen.tsx`

- [ ] Convert run history to dense operational rows with status icons and aligned metadata.
- [ ] Convert details to named sections with readable event syntax.
- [ ] Convert auto-fixes to a semantic repair ledger with no side stripe.
- [ ] Convert the allowlist into a tool summary and source rows with an actionable empty state.
- [ ] Keep polling, refresh controls, and API behavior unchanged.

Run:

```bash
cd cloud-app
npx tsc --noEmit
```

Expected: exit code 0.

### Task 6: Verify the complete redesign

**Files:**
- Verify all modified files.

- [ ] Run reducer tests.
- [ ] Run a full TypeScript check.
- [ ] Export the web bundle.
- [ ] Sweep for forbidden raw colours, emoji UI icons, ambient blobs, decorative eyebrows, glass surfaces, and side stripes.
- [ ] Inspect the rendered UI at 320, 375, 414, and 768 CSS-pixel widths when a browser surface is available.
- [ ] Inspect on an iOS simulator when a simulator is booted.
- [ ] Run Hallmark’s 58 gates and correct every applicable failure.
- [ ] Run `git diff --check` and review the final diff.

Commands:

```bash
cd cloud-app
npm test -- --runInBand
npx tsc --noEmit
npx expo export --platform web
cd ..
git diff --check
```

Expected: all commands exit 0, Jest reports no failed tests, and Expo writes a successful web export.

