import { describe, expect, test } from "vitest";
import { NextRequest } from "next/server";

import { GET } from "./route";

describe("documentation search", () => {
  test("rejects short queries without scanning", async () => {
    const response = GET(new NextRequest("http://loopguard.test/docs/search?q=x"));

    expect(await response.json()).toEqual({ query: "x", results: [] });
  });

  test("returns ranked public pages without markdown noise", async () => {
    const response = GET(
      new NextRequest("http://loopguard.test/docs/search?q=quickstart"),
    );
    const body = (await response.json()) as {
      results: Array<{ slug: string; snippet: string }>;
    };

    expect(body.results[0]?.slug).toBe("getting-started/quickstart");
    expect(body.results[0]?.snippet).not.toMatch(/[#*`\[\]]/);
    expect(response.headers.get("cache-control")).toContain("stale-while-revalidate");
  });
});
