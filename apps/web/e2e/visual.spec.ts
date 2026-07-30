import { expect, test, type Page } from "@playwright/test";

import { ids, mockControlApi } from "./fixtures";

const coreReferences = [
  { name: "inbox", path: "/inbox" },
  { name: "run-detail", path: `/runs/${ids.session}` },
  { name: "action-review", path: `/runs/${ids.session}`, target: ".action-sheet" },
  { name: "changes", path: "/changes" },
  { name: "verification", path: "/verification" },
  { name: "hosts-integrations", path: "/hosts" },
  { name: "policies", path: "/policies" },
  { name: "devices-settings", path: "/devices" },
] as const;

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-07-22T14:00:00Z"));
  await mockControlApi(page, {
    sessionOverride: {
      current_action: {
        id: "action_visual_fixture",
        state: "reviewed",
        target: { kind: "session", id: ids.session, label: "Auth migration" },
        effect: "Continue the paused run once from its verified state.",
        risk: "medium",
        parameters_hash: "sha256:visual-fixture",
        expected_state: "Run resumes from verification",
        expires_at: "2026-07-22T14:15:00Z",
        host_available: true,
      },
    },
  });
});

for (const scheme of ["light", "dark"] as const) {
  for (const reference of coreReferences) {
    test(`${reference.name} has a stable ${scheme} reference`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
      await page.setViewportSize({ width: 1280, height: 900 });
      await stablePage(page, reference.path);
      const options = {
        animations: "disabled" as const,
        caret: "hide" as const,
        maxDiffPixelRatio: 0.002,
      };
      if ("target" in reference) {
        await expect(page.locator(reference.target)).toHaveScreenshot(
          `${reference.name}-${scheme}.png`,
          options,
        );
      } else {
        await expect(page).toHaveScreenshot(`${reference.name}-${scheme}.png`, {
          ...options,
          fullPage: true,
        });
      }
    });
  }
}

for (const width of [320, 768, 1536]) {
  test(`inbox remains bounded at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await stablePage(page, "/inbox");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expect(page).toHaveScreenshot(`inbox-${width}px.png`, {
      animations: "disabled",
      caret: "hide",
      fullPage: true,
      maxDiffPixelRatio: 0.002,
    });
  });
}

test("high-contrast reduced-motion state remains legible", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark", forcedColors: "active", reducedMotion: "reduce" });
  await stablePage(page, "/inbox");
  await expect(page).toHaveScreenshot("inbox-high-contrast-reduced-motion.png", {
    animations: "disabled",
    caret: "hide",
    fullPage: true,
    maxDiffPixelRatio: 0.002,
  });
});

async function stablePage(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.locator(".async-state--loading")).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
}
