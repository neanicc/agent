import { lstat, unlink, chmod } from "node:fs/promises";
import { createServer, type Server, type Socket } from "node:net";
import { dirname, resolve } from "node:path";

export class OwnerOnlySocketTransport {
  readonly path: string;
  #server: Server | undefined;
  #identity: { dev: bigint; ino: bigint } | undefined;

  constructor(path: string) {
    this.path = path;
  }

  async start(handler: (socket: Socket) => void): Promise<void> {
    if (this.#server !== undefined) throw new Error("browser transport is already started");
    if (process.platform !== "win32") await validateUnixEndpoint(this.path);
    else validateNamedPipe(this.path);
    const server = createServer({ pauseOnConnect: true }, handler);
    this.#server = server;
    try {
      await new Promise<void>((resolveStarted, reject) => {
        const onError = (error: Error) => reject(error);
        server.once("error", onError);
        server.listen(this.path, () => {
          server.off("error", onError);
          resolveStarted();
        });
      });
      if (process.platform !== "win32") {
        await chmod(this.path, 0o600);
        const status = await lstat(this.path, { bigint: true });
        if (!status.isSocket() || status.isSymbolicLink()) throw new Error("socket path is unsafe");
        if (
          typeof process.getuid === "function" &&
          (status.uid !== BigInt(process.getuid()) || (status.mode & 0o777n) !== 0o600n)
        ) {
          throw new Error("socket path must be owner-only");
        }
        this.#identity = { dev: status.dev, ino: status.ino };
      }
      server.on("connection", (socket) => socket.resume());
    } catch (error) {
      await this.close();
      throw error;
    }
  }

  async close(): Promise<void> {
    const server = this.#server;
    this.#server = undefined;
    if (server !== undefined) {
      await new Promise<void>((resolveClosed) => server.close(() => resolveClosed()));
    }
    if (process.platform !== "win32" && this.#identity !== undefined) {
      try {
        const status = await lstat(this.path, { bigint: true });
        if (status.dev === this.#identity.dev && status.ino === this.#identity.ino && status.isSocket()) {
          await unlink(this.path);
        }
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      } finally {
        this.#identity = undefined;
      }
    }
  }
}

async function validateUnixEndpoint(path: string): Promise<void> {
  if (!resolve(path).startsWith(resolve(dirname(path)) + "/")) throw new Error("socket path is unsafe");
  const parent = await lstat(dirname(path));
  if (!parent.isDirectory() || parent.isSymbolicLink()) throw new Error("socket parent is unsafe");
  if (
    typeof process.getuid === "function" &&
    (parent.uid !== process.getuid() || (parent.mode & 0o777) !== 0o700)
  ) {
    throw new Error("socket parent must be owner-only");
  }
  try {
    await lstat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  throw new Error("socket path is unsafe because it already exists");
}

function validateNamedPipe(path: string): void {
  if (!/^\\\\\.\\pipe\\loopguard-browser-[A-Za-z0-9_-]{16,128}$/.test(path)) {
    throw new Error("named pipe path is unsafe");
  }
}
