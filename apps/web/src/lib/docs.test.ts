import { describe, expect, test } from "vitest";

import { docs, docsNavigation, markdownHeadings, readDoc } from "./docs";

describe("public documentation index", () => {
  test("contains only explicitly public sections and unique slugs", () => {
    const allowed = new Set([
      "getting-started",
      "tutorials",
      "how-to",
      "product",
      "reference",
      "security",
      "privacy",
      "operations",
      "migrations",
    ]);

    expect(docs.length).toBeGreaterThan(0);
    expect(new Set(docs.map((document) => document.slug)).size).toBe(docs.length);
    expect(docs.every((document) => allowed.has(document.section))).toBe(true);
    expect(docs.some((document) => document.slug.startsWith("superpowers/"))).toBe(false);
  });

  test("provides navigable source with stable duplicate heading anchors", () => {
    expect(readDoc("getting-started/quickstart")?.source).toContain("loopguard quickstart");
    expect(
      markdownHeadings("## Install\n### Check\n## Install").map(({ id }) => id),
    ).toEqual(["install", "check", "install-2"]);
    expect(docsNavigation().flatMap((group) => group.documents)).toHaveLength(docs.length);
  });
});
