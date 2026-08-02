import assert from "node:assert/strict";
import { mkdtemp, mkdir, lstat, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createConnection, type Socket } from "node:net";
import { BrowserBroker, OriginPolicy } from "./broker.js";
import { encodeFrame, FrameDecoder } from "./protocol.js";
import { BrowserBrokerServer } from "./server.js";
import { OwnerOnlySocketTransport } from "./transport.js";
import { FakeLauncher } from "./testing.js";

async function broker(resolveHost: (hostname: string) => Promise<string[]> = async () => ["93.184.216.34"]) {
  const root = await mkdtemp(join(tmpdir(), "loopguard-security-"));
  return BrowserBroker.start({ stateDirectory: root, launcher: new FakeLauncher(), resolveHost });
}

test("context capability is bound to its authenticated session", async () => {
  const service = await broker();
  const lease = await service.createContext({
    sessionId: "owner",
    browser: "chromium",
    storageStatePath: null,
    allowedOrigins: ["https://example.test"],
    serviceWorkers: "block",
  });
  await assert.rejects(() => service.closeContext(lease.id, "attacker"), /session mismatch/);
  await assert.rejects(() => service.runPage("x".repeat(64), "owner", []), /unknown context/);
  assert.equal(lease.context.isClosed?.(), false);
  await service.close();
});

test("navigation rejects dangerous schemes, origins, metadata, and private networks", async () => {
  const policy = new OriginPolicy({
    allowedOrigins: ["https://example.test"],
    resolveHost: async (hostname) =>
      hostname === "example.test" ? ["93.184.216.34"] : ["127.0.0.1"],
  });
  await policy.assertAllowed("https://example.test/path");
  await assert.rejects(() => policy.assertAllowed("file:///etc/passwd"), /scheme/);
  await assert.rejects(() => policy.assertAllowed("chrome-extension://abc/page"), /scheme/);
  await assert.rejects(() => policy.assertAllowed("https://other.test"), /origin/);
  await assert.rejects(() => policy.assertAllowed("http://169.254.169.254/latest/meta-data"), /origin|network/);

  const privateAllowedOrigin = new OriginPolicy({
    allowedOrigins: ["https://internal.test"],
    resolveHost: async () => ["10.0.0.2"],
  });
  await assert.rejects(() => privateAllowedOrigin.assertAllowed("https://internal.test"), /network/);
});

test("DNS rebinding is checked on every request", async () => {
  let lookups = 0;
  const policy = new OriginPolicy({
    allowedOrigins: ["https://example.test"],
    resolveHost: async () => (++lookups === 1 ? ["93.184.216.34"] : ["127.0.0.1"]),
  });
  await policy.assertAllowed("https://example.test/first");
  await assert.rejects(() => policy.assertAllowed("https://example.test/redirect"), /network/);
});

test("disconnect grace closes the session without affecting others", async () => {
  const service = await broker();
  const a = await service.createContext({
    sessionId: "a", browser: "chromium", storageStatePath: null,
    allowedOrigins: ["https://example.test"], serviceWorkers: "block",
  });
  const b = await service.createContext({
    sessionId: "b", browser: "chromium", storageStatePath: null,
    allowedOrigins: ["https://example.test"], serviceWorkers: "block",
  });
  service.clientDisconnected("a", 1);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(a.context.isClosed?.(), true);
  assert.equal(b.context.isClosed?.(), false);
  await service.close();
});

test("Unix transport creates an owner-only socket and refuses symlinks", { skip: process.platform === "win32" }, async () => {
  const root = await mkdtemp(join(tmpdir(), "loopguard-socket-"));
  await import("node:fs/promises").then(({ chmod }) => chmod(root, 0o700));
  const socket = join(root, "broker.sock");
  const transport = new OwnerOnlySocketTransport(socket);
  await transport.start(() => undefined);
  assert.equal((await lstat(socket)).mode & 0o777, 0o600);
  await transport.close();

  const target = join(root, "target");
  await mkdir(target);
  await symlink(target, socket);
  await assert.rejects(() => new OwnerOnlySocketTransport(socket).start(() => undefined), /unsafe/);
});

test("server requires the daemon capability before all broker methods", { skip: process.platform === "win32" }, async () => {
  const root = await mkdtemp(join(tmpdir(), "loopguard-server-"));
  const service = await BrowserBroker.start({
    stateDirectory: root,
    launcher: new FakeLauncher(),
    resolveHost: async () => ["93.184.216.34"],
  });
  const socketPath = join(root, "broker.sock");
  const capability = "a".repeat(64);
  const server = new BrowserBrokerServer({ broker: service, socketPath, capability });
  await server.start();
  try {
    const wrong = await connect(socketPath);
    wrong.write(encodeFrame({
      type: "hello", protocolVersion: 1, capability: "b".repeat(64),
    }));
    await closed(wrong);

    const client = await connect(socketPath);
    const responses = collect(client, 2);
    client.write(Buffer.concat([
      encodeFrame({ type: "hello", protocolVersion: 1, capability }),
      encodeFrame({ id: "health-1", method: "health", params: {} }),
    ]));
    const [handshake, health] = await responses;
    assert.deepEqual(handshake, {
      id: "handshake", ok: true, result: { protocolVersion: 1 },
    });
    assert.deepEqual(health, {
      id: "health-1", ok: true,
      result: { activeContexts: 0, activeEndpointLeases: 0, browsers: {} },
    });
    client.destroy();
  } finally {
    await server.close();
  }
});

function connect(path: string): Promise<Socket> {
  return new Promise((resolve, reject) => {
    const socket = createConnection(path, () => resolve(socket));
    socket.once("error", reject);
  });
}

function closed(socket: Socket): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("socket did not close")), 1_000);
    socket.once("close", () => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

function collect(socket: Socket, count: number): Promise<unknown[]> {
  return new Promise((resolve, reject) => {
    const decoder = new FrameDecoder();
    const values: unknown[] = [];
    const timeout = setTimeout(() => reject(new Error("broker response timed out")), 1_000);
    socket.on("data", (chunk) => {
      try {
        values.push(...decoder.push(chunk));
      } catch (error) {
        clearTimeout(timeout);
        reject(error);
      }
      if (values.length >= count) {
        clearTimeout(timeout);
        resolve(values);
      }
    });
  });
}
