import { constants } from "node:fs";
import { lstat, mkdir, open, readdir, readFile, unlink } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const MAX_LEASE_BYTES = 16 * 1024 * 1024;
const MAX_STALE_LEASES = 1_024;

export class PlaintextStorageLeaseManager {
  readonly root: string;

  private constructor(root: string) {
    this.root = root;
  }

  static async open(path: string): Promise<PlaintextStorageLeaseManager> {
    const root = resolve(path);
    await mkdir(root, { recursive: true, mode: 0o700 });
    const status = await lstat(root);
    if (!status.isDirectory() || status.isSymbolicLink()) {
      throw new Error("plaintext lease directory is a symlink or unsafe");
    }
    if (
      process.platform !== "win32" &&
      typeof process.getuid === "function" &&
      (status.uid !== process.getuid() || (status.mode & 0o777) !== 0o700)
    ) {
      throw new Error("plaintext lease directory must be owner-only");
    }
    const manager = new PlaintextStorageLeaseManager(root);
    await manager.cleanup();
    return manager;
  }

  async consume<T>(path: string, operation: (safePath: string) => Promise<T>): Promise<T> {
    const safePath = resolve(path);
    if (dirname(safePath) !== this.root) {
      throw new Error("plaintext storage state is outside its lease directory");
    }
    const status = await lstat(safePath);
    if (!status.isFile() || status.isSymbolicLink()) {
      throw new Error("plaintext storage state lease is unsafe");
    }
    if (status.size <= 0 || status.size > MAX_LEASE_BYTES) {
      throw new Error("plaintext storage state exceeds its size limit");
    }
    if (
      process.platform !== "win32" &&
      typeof process.getuid === "function" &&
      (status.uid !== process.getuid() || (status.mode & 0o777) !== 0o600)
    ) {
      throw new Error("plaintext storage state must be owner-only");
    }
    try {
      return await operation(safePath);
    } finally {
      await secureUnlink(safePath, this.root);
    }
  }

  async cleanup(): Promise<void> {
    const entries = await readdir(this.root);
    if (entries.length > MAX_STALE_LEASES) {
      throw new Error("plaintext lease directory exceeds its cleanup limit");
    }
    for (const name of entries) {
      await secureUnlink(resolve(this.root, name), this.root);
    }
  }

  async containsSecret(secret: Buffer): Promise<boolean> {
    for (const name of await readdir(this.root)) {
      const path = resolve(this.root, name);
      const status = await lstat(path);
      if (status.isFile() && !status.isSymbolicLink() && status.size <= MAX_LEASE_BYTES) {
        if ((await readFile(path)).includes(secret)) return true;
      }
    }
    return false;
  }
}

async function secureUnlink(path: string, root: string): Promise<void> {
  if (dirname(path) !== root) throw new Error("plaintext storage state is outside its lease directory");
  let status;
  try {
    status = await lstat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  if (status.isSymbolicLink()) {
    await unlink(path);
    return;
  }
  if (!status.isFile()) throw new Error("plaintext lease directory contains an unsafe entry");
  const handle = await open(path, constants.O_RDWR | (constants.O_NOFOLLOW ?? 0));
  try {
    let offset = 0;
    const zeros = Buffer.alloc(Math.min(64 * 1024, Math.max(1, status.size)));
    while (offset < status.size) {
      const length = Math.min(zeros.byteLength, status.size - offset);
      const result = await handle.write(zeros, 0, length, offset);
      if (result.bytesWritten <= 0) throw new Error("plaintext lease overwrite stalled");
      offset += result.bytesWritten;
    }
    await handle.truncate(0);
    await handle.sync();
  } finally {
    await handle.close();
  }
  await unlink(path).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT") throw error;
  });
}
