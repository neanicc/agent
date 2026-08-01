import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("documentation is public, searchable, keyboard usable, and accessible", async ({ page }) => {
  await page.goto("/docs");

  await expect(page).toHaveURL(/\/docs(?:\/)?$/);
  await expect(page.getByRole("heading", { level: 1, name: "Offline quickstart" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Documentation" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Edit this page on GitHub" })).toHaveAttribute(
    "href",
    /\/edit\/main\/docs\/getting-started\/quickstart\.md$/,
  );

  const search = page.getByLabel("Search documentation");
  await search.fill("encrypted events");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("region", { name: "Documentation search results" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Offline quickstart" }).last()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "Documentation search results" })).toHaveCount(0);

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(results.violations).toEqual([]);
});

test("documentation source forbids private implementation plans", async ({ request }) => {
  const response = await request.get("/docs/search?q=superpowers");
  const body = (await response.json()) as {
    results: Array<{ slug: string }>;
  };

  expect(response.ok()).toBe(true);
  expect(body.results.every((result) => !result.slug.startsWith("superpowers/"))).toBe(true);
});
