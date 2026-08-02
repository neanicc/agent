import { NextRequest } from "next/server";
import { beforeEach, describe, expect, test, vi } from "vitest";

const cookieGet = vi.fn();

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: cookieGet, set: vi.fn(), delete: vi.fn() }),
}));

vi.mock("@/auth", () => ({
  SESSION_COOKIE: "__Host-loopguard_session",
  refreshBrowserSession: async (session: unknown) => session,
  unsealSession: async (value: string | undefined) =>
    value === "sealed-session"
      ? { accessToken: "secret-bearer", expiresAt: 2_000_000_000, issuer: "issuer", subject: "user" }
      : null,
  sealSession: vi.fn(),
  secureCookieOptions: vi.fn(),
  webSessionTTLSeconds: () => 28_800,
}));

import { POST } from "./route";

describe("stream ticket BFF", () => {
  beforeEach(() => {
    cookieGet.mockImplementation((name: string) => {
      if (name === "__Host-loopguard_session") return { value: "sealed-session" };
      if (name === "loopguard_csrf") return { value: "csrf-value" };
      return undefined;
    });
    vi.stubEnv("CONTROL_API_URL", "https://control.example.test/api");
    vi.stubEnv("CONTROL_STREAM_URL", "wss://stream.example.test/api");
  });

  test("exchanges the HttpOnly session for a one-use URL without exposing bearer credentials", async () => {
    const upstream = vi.fn().mockResolvedValue(
      Response.json(
        { ticket: "one-use-ticket", expires_at: "2026-07-22T14:00:20Z" },
        { status: 201 },
      ),
    );
    vi.stubGlobal("fetch", upstream);
    const request = new NextRequest("https://console.example.test/api/stream-ticket", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf-value" },
      body: JSON.stringify({
        session_id: "42d0ee68-043a-40ba-bc6e-3ddf2238acbf",
        after_session_seq: 7,
      }),
    });

    const response = await POST(request);
    const payload = (await response.json()) as { stream_url: string; expires_at: string };

    expect(response.status).toBe(201);
    expect(payload.stream_url).toBe(
      "wss://stream.example.test/api/v1/sessions/42d0ee68-043a-40ba-bc6e-3ddf2238acbf/stream?ticket=one-use-ticket",
    );
    expect(JSON.stringify(payload)).not.toContain("secret-bearer");
    expect(upstream).toHaveBeenCalledWith(
      "https://control.example.test/api/v1/stream-tickets",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer secret-bearer",
          Origin: "https://console.example.test",
        }),
      }),
    );
  });

  test("rejects a missing CSRF match before contacting the control API", async () => {
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    const request = new NextRequest("https://console.example.test/api/stream-ticket", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: "42d0ee68-043a-40ba-bc6e-3ddf2238acbf",
        after_session_seq: 0,
      }),
    });

    const response = await POST(request);

    expect(response.status).toBe(403);
    expect(upstream).not.toHaveBeenCalled();
  });
});
