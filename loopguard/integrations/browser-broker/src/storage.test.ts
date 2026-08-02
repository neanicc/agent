import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { PlaintextStorageLeaseManager } from "./storage.js";

const SECRET = "cookie-secret-never-persist";

async function setup() {
  const root = await mkdtemp(join(tmpdir(), "loopguard-storage-"));
  await chmod(root, 0o700);
  return { root, manager: await PlaintextStorageLeaseManager.open(root) };
}

test("consumes an owner-only plaintext lease and unlinks it on success", async () => {
  const { root, manager } = await setup();
  const path = join(root, "lease.json");
  await writeFile(path, SECRET, { mode: 0o600 });
  const observed = await manager.consume(path, async (safePath) => readFile(safePath, "utf8"));
  assert.equal(observed, SECRET);
  assert.equal(await manager.containsSecret(Buffer.from(SECRET)), false);
});

test("unlinks plaintext after failed, timed out, and cancelled consumers", async () => {
  for (const mode of ["failure", "timeout", "cancel"] as const) {
    const { root, manager } = await setup();
    const path = join(root, `${mode}.json`);
    await writeFile(path, SECRET, { mode: 0o600 });
    await assert.rejects(
      () => manager.consume(path, async () => {
        throw new Error(mode);
      }),
      new RegExp(mode),
    );
    assert.equal(await manager.containsSecret(Buffer.from(SECRET)), false);
  }
});

test("startup removes stale leases and refuses paths outside its root", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopguard-storage-"));
  await chmod(root, 0o700);
  const stale = join(root, "stale.json");
  await writeFile(stale, SECRET, { mode: 0o600 });
  const manager = await PlaintextStorageLeaseManager.open(root);
  assert.equal(await manager.containsSecret(Buffer.from(SECRET)), false);

  const outside = join(tmpdir(), "loopguard-outside-auth.json");
  try {
    await writeFile(outside, SECRET, { mode: 0o600 });
    await assert.rejects(() => manager.consume(outside, async () => undefined), /outside/);
  } finally {
    await unlink(outside).catch(() => undefined);
  }
});
