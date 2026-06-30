# LoopGuard Phone UI Redesign

**Date:** 2026-06-30  
**Status:** Approved  
**Scope:** `cloud-app/`

## Goal

Replace the Expo app’s decorative, generated-looking visual layer with a serious
operational console while preserving every screen, action, API call, and state
transition.

## Product context

- Audience: engineers and operators supervising AI agent runs.
- Primary job: launch a run, understand its current state, and intervene quickly.
- Tone: technical, austere, calm under pressure.
- Genre: atmospheric, with decoration removed in favor of information density.

## Hallmark system

- Macrostructure family: **Workbench** for all operational screens.
- Theme: **Midnight**.
- Enrichment: none. The live product state is the visual content.
- Display: Space Grotesk, weight 600–700, roman.
- Body: IBM Plex Sans, weight 400–600.
- Telemetry: IBM Plex Mono, limited to paths, costs, tokens, and event syntax.
- Palette: cool-black paper, cool elevated surfaces, off-white ink, one cobalt
  accent. Green, amber, and red are semantic statuses only and always pair with
  text or an icon.
- Shape: 12 px controls, 16 px surfaces, pills only for compact status.
- Motion: press feedback and state crossfades only. No ambient motion.

## Structural changes

- Remove all ambient colour circles, decorative glow, glass panels, oversized
  corner radii, coloured side stripes, emoji icons, and decorative eyebrows.
- Replace the nested pill tab bar with a flat safe-area-aware native navigation
  rail using one icon family.
- Replace repeated hero blocks with compact page headers: title, one useful
  sentence, then the primary content.
- Replace card-in-card layouts with section surfaces and separated rows.
- Keep Mission Control denser than the other screens: compact metrics, clear run
  state, chronological event rows, and a visually dominant intervention panel.

## Interaction requirements

- All touch targets are at least 44 × 44 points.
- Pressables expose pressed, disabled, loading, error, and selected treatments.
- Inputs have visible labels, stable helper/error space, constant border width,
  focus treatment, and 48 point height.
- Status is never communicated by colour alone.
- Successful visible actions remain silent; errors are explicit instructions.
- Loading indicators are inline and do not create a second feedback surface.

## Screen requirements

### Connect

Show a compact LoopGuard mark, product name, server label, URL input, helper/error
line, and one connect action. Remove marketing pills and the shield emoji.

### Run

Show active runs first only when present. Project choices become separated,
pressable rows. Expanding a project reveals the mode control, optional task field,
and launch action without nesting another card.

### Mission Control

Use a compact header with back action and text-plus-icon status. Show three aligned
metrics, then the event stream. The pending decision panel stays fixed at the
bottom of the content area and uses a clear action hierarchy: approve, continue,
allow, terminate, and custom correction.

### Agents

Use dense run rows with label, status, metadata, and cost. The detail view uses
semantic sections rather than all-caps labels or coloured stripes.

### Auto-fixes

Use a chronological repair ledger. Each entry pairs a semantic status icon with
reasoning and the applied correction. No left stripe.

### Allowlist

Use a tool summary followed by source entries. Empty state explains the next
action without decorative chips.

## Verification

- Existing Jest reducer tests pass.
- TypeScript passes with `tsc --noEmit`.
- Expo web export completes.
- Source sweep finds no emoji UI icons, ambient background component, raw colours
  outside the token module, decorative eyebrows, or side-stripe borders.
- UI is checked at phone widths and on an iOS simulator when one is booted.
- Hallmark’s 58-gate slop test passes, with native-app adaptations documented.

