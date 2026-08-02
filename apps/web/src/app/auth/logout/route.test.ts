import { NextRequest } from "next/server";
import { beforeEach, describe, expect, test, vi } from "vitest";

const cookieGet = vi.fn();
const cookieDelete = vi.fn();

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: cookieGet, delete: cookieDelete }),
}));

import { POST } from "./route";

describe("web logout", () => {
  beforeEach(() => {
    cookieGet.mockImplementation((name: string) =>
      name === "loopguard_csrf" ? { value: "csrf-value" } : undefined,
    );
  });

  test("requires CSRF before deleting encrypted credentials", async () => {
    const response = await POST(
      new NextRequest("https://loopguard.test/auth/logout", { method: "POST" }),
    );

    expect(response.status).toBe(403);
    expect(cookieDelete).not.toHaveBeenCalled();
  });

  test("deletes session, transaction, and CSRF cookies", async () => {
    const response = await POST(
      new NextRequest("https://loopguard.test/auth/logout", {
        method: "POST",
        headers: { "x-csrf-token": "csrf-value" },
      }),
    );

    expect(response.status).toBe(204);
    expect(cookieDelete.mock.calls.map(([name]) => name)).toEqual([
      "__Host-loopguard_session",
      "__Host-loopguard_oidc",
      "loopguard_csrf",
    ]);
  });
});
