import { expect, test } from "@playwright/test";

import { mockControlApi } from "./fixtures";

const destinations = ["Inbox", "Runs", "Changes", "Verification", "Hosts & integrations"];

for (const width of [320, 768, 1280, 1536]) {
  test(`core navigation and content remain usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockControlApi(page);
    await page.goto("/inbox");

    if (width < 768) {
      await page.keyboard.press("Tab");
      await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
      await page.keyboard.press("Tab");
      await page.keyboard.press("Tab");
      await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();
      await page.keyboard.press("Enter");
    }

    for (const label of destinations) {
      await expect(page.getByRole("link", { name: label, exact: true })).toBeVisible();
    }
    await expect(page.getByRole("link", { name: "Repairs", exact: true })).toHaveCount(0);
    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
  });
}

test("every visible core destination resolves to a real page", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockControlApi(page);

  for (const [path, heading] of [
    ["/inbox", "Inbox"],
    ["/runs", "Runs"],
    ["/changes", "Changes"],
    ["/verification", "Verification"],
    ["/hosts", "Hosts & integrations"],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  }
});
