import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { BrowserBroker } from "./broker.js";
import { FakeLauncher, type FakePage } from "./testing.js";

async function setup(updates: { now?: () => number; idleTtlMs?: number } = {}) {
  const root = await mkdtemp(join(tmpdir(), "loopguard-browser-"));
  const launcher = new FakeLauncher();
  const broker = await BrowserBroker.start({
    launcher,
    stateDirectory: root,
    resolveHost: async () => ["93.184.216.34"],
    ...updates,
  });
  return { broker, launcher, root };
}

test("reuses browser but never context storage", async () => {
  const { broker, launcher } = await setup();
  const a = await broker.createContext({
    sessionId: "a",
    browser: "chromium",
    storageStatePath: null,
    allowedOrigins: ["https://example.test"],
    serviceWorkers: "block",
  });
  await a.context.addCookies?.([{ name: "token", value: "secret-a" }]);
  const b = await broker.createContext({
    sessionId: "b",
    browser: "chromium",
    storageStatePath: null,
    allowedOrigins: ["https://example.test"],
    serviceWorkers: "block",
  });
  assert.deepEqual(await b.context.cookies?.(), []);
  assert.equal(launcher.starts, 1);
  assert.notEqual(a.id, b.id);
  assert.equal(a.context.options?.locale, "en-US");
  assert.equal(a.context.options?.timezoneId, "UTC");
  assert.equal(a.context.options?.serviceWorkers, "block");
  await broker.close();
});

test("closing a session destroys every page, context, and capability", async () => {
  const { broker } = await setup();
  const session = await broker.createContext({
    sessionId: "a",
    browser: "chromium",
    storageStatePath: null,
    allowedOrigins: ["https://example.test"],
    serviceWorkers: "block",
  });
  const page = (await session.context.newPage()) as FakePage;
  await broker.closeContext(session.id, "a");
  assert.equal(session.context.isClosed?.(), true);
  assert.equal(page.closed, true);
  await assert.rejects(() => broker.closeContext(session.id, "a"), /unknown context/);
  await broker.close();
});

test("page commands stay in the session artifact directory", async () => {
  const { broker, root } = await setup();
  const session = await broker.createContext({
    sessionId: "a",
    browser: "chromium",
    storageStatePath: null,
    allowedOrigins: ["https://example.test"],
    serviceWorkers: "block",
  });
  await writeFile(join(session.artifactDirectory, "input.txt"), "upload");
  const result = await broker.runPage(session.id, "a", [
    { type: "goto", url: "https://example.test/path" },
    { type: "upload", selector: "input[type=file]", artifactName: "input.txt" },
    { type: "download", selector: "#download", name: "download.txt" },
    { type: "screenshot", name: "result.png", fullPage: true },
  ]);
  assert.deepEqual(result.artifacts, [
    join(session.artifactDirectory, "download.txt"),
    join(session.artifactDirectory, "result.png"),
  ]);
  assert.equal(await readFile(result.artifacts[0]!, "utf8"), "download");
  assert.equal(await readFile(result.artifacts[1]!, "utf8"), "png");
  assert.ok(session.artifactDirectory.startsWith(join(root, "artifacts")));
  await broker.close();
});

test("idle cleanup and browser crash invalidate stale capabilities", async () => {
  let clock = 1_000;
  const { broker, launcher } = await setup({ now: () => clock, idleTtlMs: 100 });
  const idle = await broker.createContext({
    sessionId: "idle",
    browser: "chromium",
    storageStatePath: null,
    allowedOrigins: ["https://example.test"],
    serviceWorkers: "block",
  });
  clock += 101;
  assert.equal(await broker.sweepIdle(), 1);
  assert.equal(idle.context.isClosed?.(), true);

  const crashed = await broker.createContext({
    sessionId: "crash",
    browser: "chromium",
    storageStatePath: null,
    allowedOrigins: ["https://example.test"],
    serviceWorkers: "block",
  });
  launcher.current?.crash();
  await new Promise((resolve) => setImmediate(resolve));
  await assert.rejects(() => broker.closeContext(crashed.id, "crash"), /unknown context/);
  await broker.createContext({
    sessionId: "restart",
    browser: "chromium",
    storageStatePath: null,
    allowedOrigins: ["https://example.test"],
    serviceWorkers: "block",
  });
  assert.equal(launcher.starts, 2);
  await broker.close();
});
