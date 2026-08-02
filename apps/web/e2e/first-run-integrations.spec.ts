import { expect, test } from "@playwright/test";

import { mockControlApi } from "./fixtures";

test("first run offers local setup without remote hook installation", async ({ page }) => {
  await mockControlApi(page, { emptyHosts: true });
  await page.goto("/hosts");

  await expect(page.getByRole("heading", { name: "Pair a host to observe your first run" })).toBeVisible();
  await expect(page.getByText("loopguard setup", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy command" })).toBeVisible();
  await expect(page.getByText("never installs global hooks remotely", { exact: false })).toBeVisible();
});
