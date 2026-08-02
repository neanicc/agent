import { expect, test } from "@playwright/test";

import { event, ids, mockControlApi } from "./fixtures";

test("a cursor gap replays before newer live events render", async ({ page }) => {
  const ticketCursors: number[] = [];
  await page.context().addCookies([
    { name: "loopguard_csrf", value: "csrf-test", url: "http://127.0.0.1:3113" },
  ]);
  await page.addInitScript(() => {
    const NativeSocket = window.WebSocket;
    class FakeSocket extends EventTarget {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = FakeSocket.OPEN;
      url: string;

      constructor(url: string | URL) {
        super();
        this.url = String(url);
        const sockets = ((window as unknown as { __loopguardSockets?: FakeSocket[] }).__loopguardSockets ??= []);
        sockets.push(this);
        queueMicrotask(() => this.dispatchEvent(new Event("open")));
      }

      close() {
        if (this.readyState === FakeSocket.CLOSED) return;
        this.readyState = FakeSocket.CLOSED;
      }

      sendEvent(value: unknown) {
        this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) }));
      }
    }
    const RoutedSocket = new Proxy(NativeSocket, {
      construct(target, argumentsList) {
        const url = String(argumentsList[0]);
        return url.startsWith("wss://stream.example.test/")
          ? new FakeSocket(url)
          : Reflect.construct(target, argumentsList);
      },
    });
    Object.defineProperty(window, "WebSocket", { configurable: true, value: RoutedSocket });
  });
  await mockControlApi(page);
  await page.route("**/api/stream-ticket", async (route) => {
    expect(route.request().headers()["x-csrf-token"]).toBe("csrf-test");
    const body = route.request().postDataJSON() as { after_session_seq: number };
    ticketCursors.push(body.after_session_seq);
    await route.fulfill({
      json: {
        stream_url: `wss://stream.example.test/v1/sessions/${ids.session}/stream?ticket=ticket-${ticketCursors.length}`,
        expires_at: "2026-07-22T14:00:20Z",
      },
      status: 201,
    });
  });
  await page.goto(`/runs/${ids.session}`);
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  const initialTicketCount = ticketCursors.length;

  await sendToLatestSocket(page, event(6, "e6", "verification_failed"));
  await expect(page.getByText("Resyncing", { exact: true })).toBeVisible();
  await expect.poll(() => ticketCursors.length).toBeGreaterThan(initialTicketCount);
  expect(ticketCursors.at(-1)).toBe(4);
  const replayTicketCount = ticketCursors.length;

  await sendToLatestSocket(page, event(5, "e5", "tool_completed"));
  await expect(page.getByText("Tool completed", { exact: true })).toBeVisible();
  await expect(page.getByText("Verification failed", { exact: true })).toBeVisible();
  await expect(page.getByText("#6", { exact: true })).toHaveCount(1);
  await expect.poll(() => ticketCursors.length).toBeGreaterThan(replayTicketCount);
  expect(ticketCursors.at(-1)).toBe(6);

  const urls = await page.evaluate(() =>
    ((window as unknown as { __loopguardSockets?: Array<{ url: string }> }).__loopguardSockets ?? []).map(
      (socket) => socket.url,
    ),
  );
  expect(urls.join(" ")).not.toContain("Bearer");
  expect(urls.join(" ")).not.toContain("secret-bearer");
});

async function sendToLatestSocket(page: import("@playwright/test").Page, value: unknown) {
  await page.evaluate((eventValue) => {
    const sockets = (window as unknown as { __loopguardSockets?: Array<{ sendEvent(value: unknown): void }> })
      .__loopguardSockets;
    if (!sockets?.length) throw new Error("No stream socket is connected");
    sockets[sockets.length - 1].sendEvent(eventValue);
  }, value);
}
