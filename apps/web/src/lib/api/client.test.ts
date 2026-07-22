import { describe, expect, test, vi } from "vitest";

import { BrowserControlClient } from "./browser-client";
import { ControlProblem } from "./errors";
import { ServerControlClient } from "./server-client";


const ok = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const problem = (status: number, code: string) =>
  new Response(
    JSON.stringify({
      type: `https://docs.loopguard.dev/errors#${code}`,
      code,
      title: "State changed",
      detail: "Fetch the current action before trying again.",
      request_id: "req-test",
      retryable: false,
      doc_url: `https://docs.loopguard.dev/errors#${code}`,
    }),
    { status, headers: { "Content-Type": "application/problem+json" } },
  );

describe("control API clients", () => {
  test("server proxy sends bearer token and request ID upstream", async () => {
    const fetcher = vi.fn().mockResolvedValue(ok({ items: [], next_cursor: null }));
    const client = new ServerControlClient({
      baseUrl: "https://api.test",
      token: async () => "token",
      fetcher,
    });

    await client.sessions();

    const request = fetcher.mock.calls[0] as [string, RequestInit];
    const headers = request[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer token");
    expect(headers["X-Request-ID"]).toMatch(/^web_[a-f0-9-]+$/);
  });

  test("browser client uses same-origin BFF and never receives a bearer token", async () => {
    const fetcher = vi.fn().mockResolvedValue(ok({ items: [], next_cursor: null }));
    const client = new BrowserControlClient({
      fetcher,
      csrfToken: () => "csrf",
    });

    await client.sessions();

    const request = fetcher.mock.calls[0] as [string, RequestInit];
    const headers = request[1].headers as Record<string, string>;
    expect(request[0]).toBe("/api/control/v1/sessions");
    expect(headers.Authorization).toBeUndefined();
    expect(headers["X-CSRF-Token"]).toBeUndefined();
  });

  test("browser state changes send CSRF but no authorization header", async () => {
    const fetcher = vi.fn().mockResolvedValue(ok({ action_id: "action-1", state: "queued" }));
    const client = new BrowserControlClient({
      fetcher,
      csrfToken: () => "csrf",
    });

    await client.createAction({
      action_id: "action-1",
      device_id: "018f0000-0000-7000-8000-000000000001",
      device_key_id: "device-key-1",
      device_algorithm: "Ed25519",
      device_signature: "signed",
    });

    const request = fetcher.mock.calls[0] as [string, RequestInit];
    const headers = request[1].headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("csrf");
    expect(headers.Authorization).toBeUndefined();
  });

  test("maps an RFC problem response from the BFF to a typed error", async () => {
    const fetcher = vi.fn().mockResolvedValue(problem(409, "stale_state"));
    const client = new BrowserControlClient({ fetcher, csrfToken: () => "csrf" });

    const pending = client.createAction({
      action_id: "action-1",
      device_id: "018f0000-0000-7000-8000-000000000001",
      device_key_id: "device-key-1",
      device_algorithm: "Ed25519",
      device_signature: "signed",
    });
    await expect(pending).rejects.toBeInstanceOf(ControlProblem);
    await expect(pending).rejects.toMatchObject({
      status: 409,
      code: "stale_state",
      requestId: "req-test",
      retryable: false,
    });
  });
});
