import { expect, test } from "@playwright/test";

import { ids, mockControlApi } from "./fixtures";


test("unavailable repair capability exposes no navigation destination", async ({
  page,
}) => {
  await mockControlApi(page);
  await page.goto("/inbox");

  await expect(
    page.getByRole("link", { name: "Repairs", exact: true }),
  ).toHaveCount(0);
});

test("fresh ready repair capability activates list and detail evidence", async ({
  page,
}) => {
  await mockControlApi(page, { repairReady: true });
  await page.goto("/inbox");

  const repairs = page.getByRole("link", { name: "Repairs", exact: true });
  await expect(repairs).toBeVisible();
  await repairs.click();
  await expect(page.getByRole("heading", { level: 1, name: "Repairs" })).toBeVisible();
  await page.goto(`/repairs/${ids.repair}`);
  await expect(page.getByRole("heading", { name: "Reproduction proof" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Candidate evidence" })).toBeVisible();
  await expect(page.getByText("Smallest fully verified compatible patch.")).toBeVisible();
  await expect(page.getByText("Explicit approval required")).toBeVisible();
  await expect(page.getByText(/merge or deploy/i)).toBeVisible();
});

test("every repair lifecycle fixture renders evidence instead of a placeholder", async ({
  page,
}) => {
  await mockControlApi(page, { repairReady: true });
  const repairIDs = Array.from({ length: 7 }, (_, index) =>
    `018f0000-0000-7000-8000-00000000030${index + 1}`,
  );

  for (const repairID of repairIDs) {
    await page.goto(`/repairs/${repairID}`);
    await expect(page.getByRole("heading", { name: "Workflow state" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Reproduction proof" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Publication" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Rollback" })).toBeVisible();
  }
});
