import { expect, test } from "@playwright/test";

import { ids, mockControlApi } from "./fixtures";

test("administration pages render only the authenticated tenant fixtures", async ({ page }) => {
  await mockControlApi(page);

  await page.goto("/policies");
  await expect(page.getByRole("heading", { name: "Policies" })).toBeVisible();
  await expect(page.getByText("wcag-contrast")).toBeVisible();
  await expect(page.getByText("Managed minimum", { exact: true })).toBeVisible();
  await expect(page.getByText("Foreign tenant policy")).toHaveCount(0);

  await page.goto("/costs");
  await expect(page.getByRole("heading", { name: "Observed cost" })).toBeVisible();
  const observedCost = page.getByRole("region", { name: "Observed cost" });
  for (const category of ["Agent", "Judge", "Verification", "Critic", "Repair"]) {
    await expect(observedCost.getByText(category, { exact: true })).toBeVisible();
  }
  await expect(page.getByText("Not estimated", { exact: false })).toBeVisible();

  await page.goto("/devices");
  await expect(page.getByText("Alice’s iPhone")).toBeVisible();
  await expect(page.getByText("Foreign tenant device")).toHaveCount(0);

  await page.goto("/audit");
  await expect(page.getByText("Policy Updated")).toBeVisible();
  await expect(page.getByText("Alice", { exact: true })).toBeVisible();
  await expect(page.getByText("req_tenant_b")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /edit|delete/i })).toHaveCount(0);
});

test("device revocation is explicit and targets only the selected device", async ({ page }) => {
  const mutations: Array<{ method: string; path: string }> = [];
  await page.context().addCookies([
    { name: "__Host-loopguard_csrf", value: "csrf-test", url: "https://127.0.0.1:3113", secure: true },
  ]);
  await mockControlApi(page, {
    onRequest: (route) => {
      if (route.request().method() !== "GET") {
        mutations.push({
          method: route.request().method(),
          path: new URL(route.request().url()).pathname,
        });
      }
    },
  });
  await page.goto("/devices");

  await page.getByRole("button", { name: "Revoke", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Revoke Alice’s iPhone?" })).toBeVisible();
  expect(mutations).toEqual([]);

  await page.getByRole("button", { name: "Confirm revoke" }).click();
  await expect.poll(() => mutations).toContainEqual({
    method: "DELETE",
    path: `/api/control/v1/devices/${ids.device}`,
  });
  expect(mutations).toHaveLength(1);
});

test("audit filter is read-only and export remains scoped to visible records", async ({ page }) => {
  await mockControlApi(page);
  await page.goto("/audit");

  await page.getByLabel("Filter audit").fill("no-match");
  await expect(page.getByRole("heading", { name: "No audit records" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Export filtered CSV" })).toBeDisabled();

  await page.getByLabel("Filter audit").fill("policy");
  await expect(page.getByText("Policy Updated")).toBeVisible();
  await expect(page.getByRole("button", { name: "Export filtered CSV" })).toBeEnabled();
});
