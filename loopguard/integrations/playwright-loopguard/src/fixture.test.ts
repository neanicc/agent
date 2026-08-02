import assert from "node:assert/strict";
import test from "node:test";
import { brokerEnvironment } from "./config.js";
import { runBrowserWorker, type EndpointBroker } from "./fixture.js";

class FakeBrowser {
  closed = false;
  contextClosed = false;
  contexts() {
    return [{ close: async () => { this.contextClosed = true; } }];
  }
  async close() { this.closed = true; }
}

class FakeBroker implements EndpointBroker {
  released: string[] = [];
  closed = false;
  async acquire() {
    return { endpoint: "ws://broker.test/playwright", leaseId: "l".repeat(64) };
  }
  async release(leaseId: string) { this.released.push(leaseId); }
  async close() { this.closed = true; }
}

test("uses the broker endpoint and preserves the shared browser process", async () => {
  const broker = new FakeBroker();
  const remote = new FakeBrowser();
  let endpoint = "";
  const mode = await runBrowserWorker(
    {
      openBroker: async () => broker,
      browserName: "chromium",
      connect: async (value) => { endpoint = value; return remote; },
      launchNative: async () => { throw new Error("native should not launch"); },
      allowNativeFallback: true,
    },
    async () => undefined,
  );
  assert.equal(mode, "broker");
  assert.equal(endpoint, "ws://broker.test/playwright");
  assert.equal(remote.contextClosed, true);
  assert.equal(remote.closed, false);
  assert.deepEqual(broker.released, ["l".repeat(64)]);
  assert.equal(broker.closed, true);
});

test("falls back to native Playwright when the broker is unavailable", async () => {
  const native = new FakeBrowser();
  const mode = await runBrowserWorker(
    {
      openBroker: async () => { throw new Error("offline"); },
      browserName: "webkit",
      connect: async () => { throw new Error("connect should not run"); },
      launchNative: async () => native,
      allowNativeFallback: true,
    },
    async (browser) => assert.equal(browser, native),
  );
  assert.equal(mode, "native");
  assert.equal(native.contextClosed, true);
  assert.equal(native.closed, true);
});

test("failure cleanup releases a broker lease without stopping the warm browser", async () => {
  const broker = new FakeBroker();
  const remote = new FakeBrowser();
  await assert.rejects(
    runBrowserWorker(
      {
        openBroker: async () => broker,
        browserName: "firefox",
        connect: async () => remote,
        launchNative: async () => remote,
        allowNativeFallback: false,
      },
      async () => { throw new Error("test failed"); },
    ),
    /test failed/,
  );
  assert.equal(remote.contextClosed, true);
  assert.equal(remote.closed, false);
  assert.deepEqual(broker.released, ["l".repeat(64)]);
});

test("requires a complete, bounded broker environment", () => {
  assert.equal(brokerEnvironment({}), null);
  assert.equal(
    brokerEnvironment({
      LOOPGUARD_BROWSER_SOCKET: "/tmp/browser.sock",
      LOOPGUARD_BROWSER_CAPABILITY: "c".repeat(64),
      LOOPGUARD_BROWSER_SESSION: "session-1",
      LOOPGUARD_BROWSER_REQUIRED: "1",
    })?.allowNativeFallback,
    false,
  );
  assert.throws(() => brokerEnvironment({
    LOOPGUARD_BROWSER_SOCKET: "/tmp/browser.sock",
    LOOPGUARD_BROWSER_CAPABILITY: "short",
    LOOPGUARD_BROWSER_SESSION: "session-1",
  }));
});
