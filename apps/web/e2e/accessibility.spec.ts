import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { ids, mockControlApi } from "./fixtures";

const coreRoutes = [
  "/inbox",
  "/runs",
  `/runs/${ids.session}`,
  "/changes",
  `/changes/${ids.change}`,
  "/verification",
  `/verification/${ids.verification}`,
  "/hosts",
  `/hosts/${ids.host}`,
  "/policies",
  "/costs",
  "/devices",
  "/audit",
] as const;

test.beforeEach(async ({ page }) => {
  await mockControlApi(page);
});

for (const route of coreRoutes) {
  test(`${route} has no configured WCAG 2.2 AA violations`, async ({ page }) => {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();

    expect(results.violations).toEqual([]);
  });
}

test("skip link and primary navigation work with a keyboard", async ({ page }) => {
  await page.goto("/inbox");

  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();

  await page.goto("/inbox");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Runs", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/runs$/);
  await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
});

test("console exposes one main landmark, named navigation, and ordered headings", async ({ page }) => {
  await page.goto("/inbox");

  await expect(page.getByRole("main")).toHaveCount(1);
  await expect(page.getByRole("navigation", { name: "Control console" })).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1, name: "Inbox" })).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 2, name: "Needs attention" })).toBeVisible();
});

test("failed data is announced and recoverable without relying on colour", async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  await mockControlApi(page, { failPath: "/v1/sessions" });
  await page.goto("/runs");

  const announcement = page.locator('[aria-live="polite"]');
  await expect(announcement).toContainText("This view could not be loaded");
  await expect(page.getByRole("button", { name: "Retry request" })).toBeVisible();
});

test("status meaning survives colour removal and reduced motion", async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  await mockControlApi(page, { delayMs: 800 });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.goto("/inbox");
  await expect(page.locator(".skeleton").first()).toBeVisible();
  const animationDuration = await page.locator(".skeleton").evaluateAll((elements) =>
    elements.map((element) => getComputedStyle(element).animationDuration),
  );
  expect(animationDuration.every((duration) => duration === "0.15s" || duration === "0s")).toBe(true);

  await page.locator("html").evaluate((element) => {
    element.style.filter = "grayscale(1)";
  });

  await expect(page.getByText("Blocked", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Passed", { exact: true }).first()).toBeVisible();
});
