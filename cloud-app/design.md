# Design — LoopGuard Cloud

A locked design system for the Expo phone app. Every screen reads this system
before changing its visual layer. The native token implementation lives in
`src/theme.ts`; `tokens.css` is the portable OKLCH source.

## Genre

Atmospheric, stripped to an austere operational register. The interface is dark
because it is a monitoring surface, not because it needs decorative glow.

## Macrostructure family

- Marketing pages: none.
- App pages: **Workbench** — compact headers, state-led sections, separated rows,
  and dense live telemetry.
- Content pages: **Long Document** only for future documentation surfaces.

## Theme

- `--color-paper` oklch(13% 0.018 255)
- `--color-paper-2` oklch(17% 0.022 255)
- `--color-paper-3` oklch(21% 0.024 255)
- `--color-ink` oklch(96% 0.008 255)
- `--color-ink-2` oklch(76% 0.016 255)
- `--color-rule` oklch(32% 0.024 255)
- `--color-accent` oklch(70% 0.17 250)
- `--color-focus` oklch(82% 0.12 245)

Green, amber, and red are semantic statuses only. They must pair with an icon or
text label and never become decorative accents.

## Typography

- Display: Space Grotesk, weights 600 and 700, style normal.
- Body: IBM Plex Sans, weights 400, 500, and 600.
- Mono: IBM Plex Mono, weights 400, 500, and 600.
- Display tracking: -0.025 em.
- Native type scale anchor: 32 pt page display; 16 pt body.
- Mono is limited to telemetry, paths, costs, tokens, and event syntax.

## Spacing

A 4-point named scale. Native code uses `theme.space(n)` and semantic component
spacing. Raw one-off margins and padding are not allowed.

## Motion

- Easings: `--ease-out`, `--ease-in`, and `--ease-in-out`.
- Native motion: press feedback and short state crossfades only.
- Reduced-motion fallback: no spatial movement; opacity-only at 150 ms or less.
- No ambient, looping, bouncing, or scroll-triggered animation.

## Microinteractions stance

- Silent success when the changed state is visible.
- Inline loading inside the control that initiated the request.
- Press feedback uses opacity and a 1 px translation.
- Focus feedback is immediate and never animated.
- Touch targets are at least 44 × 44 points.

## CTA voice

- Primary action: 48 pt high, off-white ink fill, dark text, 12 px radius, direct
  verb-first label. Cobalt marks selection, focus, and navigation rather than
  filling large surfaces.
- Secondary action: raised surface, strong boundary, off-white ink.
- Destructive action: dark surface, red icon and label, never a full red panel.

## Per-page allowances

- Connect may use the LoopGuard monogram as its only focal mark.
- Mission Control may use tighter vertical rhythm than other screens.
- No app page uses enrichment, ambient blobs, gradients, glass, or fake chrome.

## What pages MUST share

- Palette, typography, icon voice, status semantics, control heights, radii,
  compact page-header rhythm, and bottom navigation.
- One cobalt accent. Semantic statuses are exceptions, not theme colours.
- SF Symbols on iOS and one Feather fallback family elsewhere.

## What pages MAY differ on

- Information density.
- Whether content is row-led, event-led, or form-led.
- Presence of a fixed intervention surface during an active run.

## Exports

### tokens.css

The canonical CSS export is [`tokens.css`](tokens.css).

### Tailwind v4 `@theme`

```css
@theme {
  --color-paper: oklch(13% 0.018 255);
  --color-paper-2: oklch(17% 0.022 255);
  --color-paper-3: oklch(21% 0.024 255);
  --color-raised: oklch(24.5% 0.025 255);
  --color-rule: oklch(32% 0.024 255);
  --color-rule-strong: oklch(48% 0.024 255);
  --color-ink: oklch(96% 0.008 255);
  --color-ink-2: oklch(76% 0.016 255);
  --color-muted: oklch(66% 0.018 255);
  --color-accent: oklch(70% 0.17 250);
  --color-accent-ink: oklch(15% 0.025 255);
  --color-focus: oklch(82% 0.12 245);
  --color-success: oklch(73% 0.14 155);
  --color-warning: oklch(80% 0.14 85);
  --color-danger: oklch(68% 0.19 25);
  --font-display: "Space Grotesk", sans-serif;
  --font-body: "IBM Plex Sans", sans-serif;
  --font-outlier: "IBM Plex Mono", monospace;
  --spacing-3xs: 0.125rem;
  --spacing-2xs: 0.25rem;
  --spacing-xs: 0.5rem;
  --spacing-sm: 0.75rem;
  --spacing-md: 1rem;
  --spacing-lg: 1.5rem;
  --spacing-xl: 2.5rem;
  --spacing-2xl: 4rem;
  --spacing-3xl: 6rem;
  --text-xs: 0.75rem;
  --text-sm: 0.875rem;
  --text-base: 1rem;
  --text-md: 1.25rem;
  --text-lg: 1.5625rem;
  --text-xl: 1.9531rem;
  --text-2xl: 2.4414rem;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in: cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
  --radius-control: 0.75rem;
  --radius-surface: 1rem;
  --radius-status: 999px;
}
```

### DTCG `tokens.json`

```json
{
  "$schema": "https://design-tokens.github.io/community-group/format/",
  "color": {
    "paper": { "$value": "oklch(13% 0.018 255)", "$type": "color" },
    "paper-2": { "$value": "oklch(17% 0.022 255)", "$type": "color" },
    "paper-3": { "$value": "oklch(21% 0.024 255)", "$type": "color" },
    "raised": { "$value": "oklch(24.5% 0.025 255)", "$type": "color" },
    "rule": { "$value": "oklch(32% 0.024 255)", "$type": "color" },
    "rule-strong": { "$value": "oklch(48% 0.024 255)", "$type": "color" },
    "ink": { "$value": "oklch(96% 0.008 255)", "$type": "color" },
    "ink-2": { "$value": "oklch(76% 0.016 255)", "$type": "color" },
    "muted": { "$value": "oklch(66% 0.018 255)", "$type": "color" },
    "accent": { "$value": "oklch(70% 0.17 250)", "$type": "color" },
    "accent-ink": { "$value": "oklch(15% 0.025 255)", "$type": "color" },
    "focus": { "$value": "oklch(82% 0.12 245)", "$type": "color" },
    "success": { "$value": "oklch(73% 0.14 155)", "$type": "color" },
    "warning": { "$value": "oklch(80% 0.14 85)", "$type": "color" },
    "danger": { "$value": "oklch(68% 0.19 25)", "$type": "color" }
  },
  "font": {
    "display": { "$value": "Space Grotesk", "$type": "fontFamily" },
    "body": { "$value": "IBM Plex Sans", "$type": "fontFamily" },
    "outlier": { "$value": "IBM Plex Mono", "$type": "fontFamily" }
  },
  "size": {
    "text-xs": { "$value": "0.75rem", "$type": "dimension" },
    "text-sm": { "$value": "0.875rem", "$type": "dimension" },
    "text-base": { "$value": "1rem", "$type": "dimension" },
    "text-md": { "$value": "1.25rem", "$type": "dimension" },
    "text-lg": { "$value": "1.5625rem", "$type": "dimension" },
    "text-xl": { "$value": "1.9531rem", "$type": "dimension" },
    "text-2xl": { "$value": "2.4414rem", "$type": "dimension" }
  },
  "space": {
    "3xs": { "$value": "0.125rem", "$type": "dimension" },
    "2xs": { "$value": "0.25rem", "$type": "dimension" },
    "xs": { "$value": "0.5rem", "$type": "dimension" },
    "sm": { "$value": "0.75rem", "$type": "dimension" },
    "md": { "$value": "1rem", "$type": "dimension" },
    "lg": { "$value": "1.5rem", "$type": "dimension" },
    "xl": { "$value": "2.5rem", "$type": "dimension" },
    "2xl": { "$value": "4rem", "$type": "dimension" },
    "3xl": { "$value": "6rem", "$type": "dimension" }
  },
  "duration": {
    "micro": { "$value": "100ms", "$type": "duration" },
    "short": { "$value": "180ms", "$type": "duration" },
    "long": { "$value": "300ms", "$type": "duration" }
  },
  "radius": {
    "control": { "$value": "0.75rem", "$type": "dimension" },
    "surface": { "$value": "1rem", "$type": "dimension" },
    "status": { "$value": "999px", "$type": "dimension" }
  }
}
```

### shadcn/ui CSS variables

```css
:root {
  --background: 13% 0.018 255;
  --foreground: 96% 0.008 255;
  --card: 17% 0.022 255;
  --card-foreground: 96% 0.008 255;
  --popover: 17% 0.022 255;
  --popover-foreground: 96% 0.008 255;
  --primary: 70% 0.17 250;
  --primary-foreground: 15% 0.025 255;
  --secondary: 21% 0.024 255;
  --secondary-foreground: 96% 0.008 255;
  --muted: 32% 0.024 255;
  --muted-foreground: 66% 0.018 255;
  --accent: 70% 0.17 250;
  --accent-foreground: 15% 0.025 255;
  --destructive: 68% 0.19 25;
  --destructive-foreground: 15% 0.025 255;
  --border: 32% 0.024 255;
  --input: 48% 0.024 255;
  --ring: 82% 0.12 245;
  --radius: 0.75rem;
}
```

## Native colour export

React Native 0.74 uses opaque sRGB token values for consistent iOS, Android, and
web rendering. `src/theme.ts` contains the converted values. Raw colour values
must appear only in that token module.
