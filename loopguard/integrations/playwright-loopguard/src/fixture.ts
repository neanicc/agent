import { randomUUID } from "node:crypto";
import { createConnection, type Socket } from "node:net";
import {
  test as base,
  expect,
  type Browser,
} from "@playwright/test";
import { brokerEnvironment, type BrokerEnvironment } from "./config.js";

const MAX_FRAME_BYTES = 1_048_576;
const REQUEST_TIMEOUT_MS = 15_000;

type BrowserLike = {
  contexts(): Array<{ close(): Promise<unknown> }>;
  close(): Promise<unknown>;
};

export type EndpointBroker = {
  acquire(browser: "chromium" | "firefox" | "webkit"): Promise<{
    endpoint: string;
    leaseId: string;
  }>;
  release(leaseId: string): Promise<void>;
  close(): Promise<void>;
};

export async function runBrowserWorker<T extends BrowserLike>(
  options: {
    openBroker: (() => Promise<EndpointBroker>) | null;
    browserName: "chromium" | "firefox" | "webkit";
    connect(endpoint: string): Promise<T>;
    launchNative(): Promise<T>;
    allowNativeFallback: boolean;
  },
  use: (browser: T) => Promise<void>,
): Promise<"broker" | "native"> {
  let broker: EndpointBroker | undefined;
  let leaseId: string | undefined;
  let browser: T | undefined;
  let mode: "broker" | "native" = "broker";
  try {
    try {
      if (options.openBroker === null) throw new Error("broker is not configured");
      broker = await options.openBroker();
      const lease = await broker.acquire(options.browserName);
      leaseId = lease.leaseId;
      browser = await options.connect(lease.endpoint);
    } catch (error) {
      if (!options.allowNativeFallback) throw error;
      if (leaseId !== undefined) await broker?.release(leaseId).catch(() => undefined);
      leaseId = undefined;
      await broker?.close().catch(() => undefined);
      broker = undefined;
      browser = await options.launchNative();
      mode = "native";
    }
    await use(browser);
    return mode;
  } finally {
    if (browser !== undefined) {
      await Promise.allSettled(browser.contexts().map((context) => context.close()));
      if (mode === "native") await browser.close().catch(() => undefined);
    }
    if (leaseId !== undefined) await broker?.release(leaseId).catch(() => undefined);
    await broker?.close().catch(() => undefined);
  }
}

/** Drop-in Playwright Test export. Test bodies continue to use the standard fixtures. */
export const test = base.extend({
  browser: [
    async (
      { playwright, browserName, headless, channel, launchOptions },
      use: (browser: Browser) => Promise<void>,
      workerInfo,
    ) => {
      const environment = brokerEnvironment();
      const browserType = playwright[browserName];
      await runBrowserWorker(
        {
          openBroker:
            environment === null
              ? null
              : () => SocketEndpointBroker.connect(environment, workerInfo.workerIndex),
          browserName,
          connect: (endpoint) => browserType.connect(endpoint),
          launchNative: () =>
            browserType.launch({
              ...launchOptions,
              headless,
              ...(channel === undefined ? {} : { channel }),
            }),
          allowNativeFallback: environment?.allowNativeFallback ?? true,
        },
        use,
      );
    },
    { scope: "worker" },
  ],
});
export { expect };

class SocketEndpointBroker implements EndpointBroker {
  readonly #channel: FrameChannel;
  readonly #sessionId: string;

  private constructor(channel: FrameChannel, sessionId: string) {
    this.#channel = channel;
    this.#sessionId = sessionId;
  }

  static async connect(
    environment: BrokerEnvironment,
    workerIndex: number,
  ): Promise<SocketEndpointBroker> {
    const channel = await FrameChannel.connect(environment.socketPath);
    try {
      const response = await channel.request({
        type: "hello",
        protocolVersion: 1,
        capability: environment.capability,
      });
      const handshake = assertSuccess(response, "handshake");
      if (handshake.protocolVersion !== 1) {
        throw new Error("LoopGuard browser broker protocol version is incompatible");
      }
      return new SocketEndpointBroker(
        channel,
        `${environment.sessionId}:${process.pid}:${workerIndex}`,
      );
    } catch (error) {
      await channel.close();
      throw error;
    }
  }

  async acquire(browser: "chromium" | "firefox" | "webkit") {
    const id = `connect-${randomUUID()}`;
    const response = await this.#channel.request({
      id,
      method: "browser.connect",
      params: { sessionId: this.#sessionId, browser },
    });
    const result = assertSuccess(response, id);
    if (
      typeof result.endpoint !== "string" ||
      !/^wss?:\/\//.test(result.endpoint) ||
      typeof result.leaseId !== "string" ||
      !/^[A-Za-z0-9_-]{32,256}$/.test(result.leaseId)
    ) {
      throw new Error("LoopGuard browser endpoint response is invalid");
    }
    return { endpoint: result.endpoint, leaseId: result.leaseId };
  }

  async release(leaseId: string): Promise<void> {
    const id = `release-${randomUUID()}`;
    const response = await this.#channel.request({
      id,
      method: "browser.release",
      params: { sessionId: this.#sessionId, leaseId },
    });
    assertSuccess(response, id);
  }

  close(): Promise<void> {
    return this.#channel.close();
  }
}

class FrameChannel {
  readonly #socket: Socket;
  #buffer = Buffer.alloc(0);
  #frames: unknown[] = [];
  #waiter: { resolve(value: unknown): void; reject(error: Error): void } | undefined;
  #closed = false;

  private constructor(socket: Socket) {
    this.#socket = socket;
    socket.on("data", (chunk) => this.#receive(chunk));
    socket.on("error", () => this.#fail(new Error("LoopGuard browser broker connection failed")));
    socket.on("close", () => this.#fail(new Error("LoopGuard browser broker connection closed")));
  }

  static async connect(path: string): Promise<FrameChannel> {
    const socket = createConnection(path);
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        socket.destroy();
        reject(new Error("LoopGuard browser broker connection timed out"));
      }, REQUEST_TIMEOUT_MS);
      socket.once("connect", () => {
        clearTimeout(timeout);
        socket.off("error", reject);
        resolve();
      });
      socket.once("error", reject);
    });
    return new FrameChannel(socket);
  }

  async request(body: unknown): Promise<unknown> {
    if (this.#closed || this.#waiter !== undefined) {
      throw new Error("LoopGuard browser broker channel is unavailable");
    }
    const payload = Buffer.from(JSON.stringify(body), "utf8");
    if (payload.byteLength === 0 || payload.byteLength > MAX_FRAME_BYTES) {
      throw new Error("LoopGuard browser broker request is too large");
    }
    const frame = Buffer.allocUnsafe(payload.byteLength + 4);
    frame.writeUInt32BE(payload.byteLength, 0);
    payload.copy(frame, 4);
    this.#socket.write(frame);
    if (this.#frames.length > 0) return this.#frames.shift();
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.#waiter = undefined;
        reject(new Error("LoopGuard browser broker request timed out"));
      }, REQUEST_TIMEOUT_MS);
      this.#waiter = {
        resolve: (value) => {
          clearTimeout(timeout);
          resolve(value);
        },
        reject: (error) => {
          clearTimeout(timeout);
          reject(error);
        },
      };
    });
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    this.#socket.end();
    this.#socket.destroy();
  }

  #receive(chunk: Buffer): void {
    this.#buffer = Buffer.concat([this.#buffer, chunk]);
    while (this.#buffer.byteLength >= 4) {
      const length = this.#buffer.readUInt32BE(0);
      if (length === 0 || length > MAX_FRAME_BYTES) {
        this.#fail(new Error("LoopGuard browser broker response is too large"));
        return;
      }
      if (this.#buffer.byteLength < length + 4) return;
      const payload = this.#buffer.subarray(4, length + 4);
      this.#buffer = this.#buffer.subarray(length + 4);
      let value: unknown;
      try {
        value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(payload));
      } catch {
        this.#fail(new Error("LoopGuard browser broker response is malformed"));
        return;
      }
      const waiter = this.#waiter;
      if (waiter !== undefined) {
        this.#waiter = undefined;
        waiter.resolve(value);
      } else {
        if (this.#frames.length >= 128) {
          this.#fail(new Error("LoopGuard browser broker response queue overflowed"));
          return;
        }
        this.#frames.push(value);
      }
    }
  }

  #fail(error: Error): void {
    if (this.#closed) return;
    this.#closed = true;
    this.#socket.destroy();
    const waiter = this.#waiter;
    this.#waiter = undefined;
    waiter?.reject(error);
  }
}

function assertSuccess(value: unknown, id: string): Record<string, unknown> {
  if (
    typeof value !== "object" ||
    value === null ||
    (value as { id?: unknown }).id !== id ||
    (value as { ok?: unknown }).ok !== true ||
    typeof (value as { result?: unknown }).result !== "object" ||
    (value as { result?: unknown }).result === null
  ) {
    throw new Error("LoopGuard browser broker rejected the request");
  }
  return (value as { result: Record<string, unknown> }).result;
}
