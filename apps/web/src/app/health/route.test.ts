import { afterEach, describe, expect, test } from "vitest";

import { GET } from "./route";

describe("web health", () => {
  const originalBuildSha = process.env.BUILD_SHA;

  afterEach(() => {
    if (originalBuildSha === undefined) {
      delete process.env.BUILD_SHA;
    } else {
      process.env.BUILD_SHA = originalBuildSha;
    }
  });

  test("identifies the immutable build", async () => {
    process.env.BUILD_SHA = "abc123";

    const response = await GET();

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ status: "ok", build_sha: "abc123" });
  });

  test("fails closed when immutable build identity is absent", async () => {
    delete process.env.BUILD_SHA;

    const response = await GET();

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ status: "degraded", build_sha: "unavailable" });
  });
});
