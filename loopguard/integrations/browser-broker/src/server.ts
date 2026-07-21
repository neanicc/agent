import { timingSafeEqual } from "node:crypto";
import type { Socket } from "node:net";
import { pathToFileURL } from "node:url";
import { BrowserBroker } from "./broker.js";
import {
  encodeFrame,
  FrameDecoder,
  MAX_PENDING_REQUESTS,
  parseCommand,
  parseHandshake,
  type BrokerCommand,
} from "./protocol.js";
import { OwnerOnlySocketTransport } from "./transport.js";

export class BrowserBrokerServer {
  readonly #broker: BrowserBroker;
  readonly #transport: OwnerOnlySocketTransport;
  readonly #capability: Buffer;
  readonly #connections = new Set<Socket>();

  constructor(options: { broker: BrowserBroker; socketPath: string; capability: string }) {
    if (!/^[A-Za-z0-9_-]{43,128}$/.test(options.capability)) {
      throw new Error("connection capability is invalid");
    }
    this.#broker = options.broker;
    this.#transport = new OwnerOnlySocketTransport(options.socketPath);
    this.#capability = Buffer.from(options.capability);
  }

  async start(): Promise<void> {
    await this.#transport.start((socket) => this.#accept(socket));
  }

  async close(): Promise<void> {
    for (const socket of this.#connections) socket.destroy();
    this.#connections.clear();
    await this.#transport.close();
    await this.#broker.close();
  }

  #accept(socket: Socket): void {
    this.#connections.add(socket);
    const decoder = new FrameDecoder();
    let authenticated = false;
    let sessionId: string | undefined;
    let pending = 0;
    let chain = Promise.resolve();

    socket.on("data", (chunk) => {
      let values: unknown[];
      try {
        values = decoder.push(chunk);
      } catch {
        socket.destroy();
        return;
      }
      for (const value of values) {
        pending += 1;
        if (pending > MAX_PENDING_REQUESTS) {
          socket.destroy();
          return;
        }
        chain = chain
          .then(async () => {
            if (!authenticated) {
              const handshake = parseHandshake(value);
              const received = Buffer.from(handshake.capability);
              if (
                received.byteLength !== this.#capability.byteLength ||
                !timingSafeEqual(received, this.#capability)
              ) {
                throw new Error("authentication failed");
              }
              authenticated = true;
              this.#write(socket, { id: "handshake", ok: true, result: { protocolVersion: 1 } });
              return;
            }
            const command = parseCommand(value);
            if (command.method === "context.create") {
              if (sessionId !== undefined && sessionId !== command.params.sessionId) {
                throw new Error("connection session mismatch");
              }
              sessionId = command.params.sessionId;
            }
            this.#write(socket, await this.#execute(command, sessionId));
          })
          .catch(() => {
            socket.destroy();
          })
          .finally(() => {
            pending -= 1;
          });
      }
    });
    socket.once("close", () => {
      this.#connections.delete(socket);
      if (sessionId !== undefined) this.#broker.clientDisconnected(sessionId);
    });
    socket.once("error", () => undefined);
  }

  async #execute(command: BrokerCommand, sessionId: string | undefined): Promise<unknown> {
    try {
      switch (command.method) {
        case "health":
          return { id: command.id, ok: true, result: this.#broker.health() };
        case "context.create": {
          const lease = await this.#broker.createContext(command.params);
          return {
            id: command.id,
            ok: true,
            result: { contextId: lease.id, artifactDirectory: lease.artifactDirectory },
          };
        }
        case "context.close":
          if (sessionId === undefined) throw new Error("connection has no session");
          await this.#broker.closeContext(command.params.contextId, sessionId);
          return { id: command.id, ok: true, result: {} };
        case "page.run":
          if (sessionId === undefined) throw new Error("connection has no session");
          return {
            id: command.id,
            ok: true,
            result: await this.#broker.runPage(
              command.params.contextId,
              sessionId,
              command.params.actions,
            ),
          };
      }
    } catch (error) {
      return {
        id: command.id,
        ok: false,
        error: { code: "request_failed", message: safeMessage(error) },
      };
    }
  }

  #write(socket: Socket, value: unknown): void {
    if (!socket.destroyed) socket.write(encodeFrame(value));
  }
}

function safeMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "broker request failed";
  const allowed = new Set([
    "session already owns a browser context",
    "unknown context capability",
    "context session mismatch",
    "connection has no session",
    "connection session mismatch",
    "navigation URL is invalid",
    "navigation scheme is denied",
    "navigation credentials are denied",
    "navigation origin is not allowed",
    "navigation network is denied",
    "navigation host did not resolve",
    "artifact name is unsafe",
    "page action limit exceeded",
  ]);
  return allowed.has(message) ? message : "broker request failed";
}

async function main(): Promise<void> {
  const socketIndex = process.argv.indexOf("--socket");
  const stateIndex = process.argv.indexOf("--state-directory");
  const socketPath = socketIndex >= 0 ? process.argv[socketIndex + 1] : undefined;
  const stateDirectory = stateIndex >= 0 ? process.argv[stateIndex + 1] : undefined;
  const capability = process.env.LOOPGUARD_BROWSER_CAPABILITY;
  if (!socketPath || !stateDirectory || !capability) {
    throw new Error("broker startup arguments are incomplete");
  }
  delete process.env.LOOPGUARD_BROWSER_CAPABILITY;
  const broker = await BrowserBroker.start({ stateDirectory });
  const server = new BrowserBrokerServer({ broker, socketPath, capability });
  await server.start();
  const stop = async () => {
    await server.close();
    process.exitCode = 0;
  };
  process.once("SIGINT", () => void stop());
  process.once("SIGTERM", () => void stop());
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  void main().catch(() => {
    process.exitCode = 1;
  });
}
