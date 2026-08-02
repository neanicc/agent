import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { BrowserBroker } from "../../browser-broker/src/broker.js";
import { BrowserBrokerServer } from "../../browser-broker/src/server.js";

const execute = promisify(execFile);
const packageRoot = join(import.meta.dirname, "..");
const playwrightCli = join(packageRoot, "node_modules", "@playwright", "test", "cli.js");

test("real Playwright projects preserve semantics while reusing the broker process", { timeout: 120_000 }, async () => {
  const project = await mkdtemp(join(packageRoot, ".integration-"));
  const brokerState = await mkdtemp(join(tmpdir(), "loopguard-playwright-broker-"));
  const socketPath = join(brokerState, "broker.sock");
  const capability = randomBytes(48).toString("base64url");
  const broker = await BrowserBroker.start({
    stateDirectory: brokerState,
    resolveHost: async () => ["93.184.216.34"],
  });
  const server = new BrowserBrokerServer({ broker, socketPath, capability });
  await server.start();
  try {
    await writeProject(project);

    await runPlaywright(project, "native.config.ts", {});
    await runPlaywright(project, "accelerated.config.ts", brokerEnv(socketPath, capability));
    const firstLease = await broker.acquireEndpoint("probe-first", "chromium");
    await broker.releaseEndpoint(firstLease.leaseId, "probe-first");

    await runPlaywright(project, "accelerated.config.ts", brokerEnv(socketPath, capability));
    const secondLease = await broker.acquireEndpoint("probe-second", "chromium");
    await broker.releaseEndpoint(secondLease.leaseId, "probe-second");
    assert.equal(secondLease.endpoint, firstLease.endpoint);
    assert.equal(broker.health().activeEndpointLeases, 0);

    const report = JSON.parse(
      await import("node:fs/promises").then(({ readFile }) =>
        readFile(join(project, "report.json"), "utf8"),
      ),
    ) as { stats?: { expected?: number } };
    assert.equal(report.stats?.expected, 2);
    assert.ok((await filesBelow(join(project, "artifacts"))).some((path) => path.endsWith("trace.zip")));

    await assert.rejects(
      runPlaywright(project, "failure.config.ts", brokerEnv(socketPath, capability)),
    );
    assert.equal(broker.health().activeEndpointLeases, 0);

    await runPlaywright(project, "accelerated.config.ts", {});
  } finally {
    await server.close();
    await rm(project, { recursive: true, force: true });
    await rm(brokerState, { recursive: true, force: true });
  }
});

async function writeProject(project: string): Promise<void> {
  await writeFile(
    join(project, "accelerated.spec.ts"),
    `import { test, expect } from "@loopguard/playwright";
test("worker one gets isolated storage", async ({ context, page }) => {
  await context.addCookies([{ name: "token", value: "secret", domain: "example.test", path: "/" }]);
  await page.setContent("<main>accelerated</main>");
  await expect(page.locator("main")).toHaveText("accelerated");
});
test("worker two cannot see prior storage", async ({ context }) => {
  expect(await context.cookies()).toEqual([]);
});
`,
  );
  await writeFile(
    join(project, "native.spec.ts"),
    `import { test, expect } from "@playwright/test";
test("native baseline", async ({ page }) => {
  await page.setContent("<main>native</main>");
  await expect(page.locator("main")).toHaveText("native");
});
`,
  );
  await writeFile(
    join(project, "failure.spec.ts"),
    `import { test, expect } from "@loopguard/playwright";
test("failure still tears down", async ({ page }) => {
  await page.setContent("<main>failure</main>");
  expect(1).toBe(2);
});
`,
  );
  await writeFile(join(project, "native.config.ts"), config("native.spec.ts", "off", false));
  await writeFile(
    join(project, "accelerated.config.ts"),
    config("accelerated.spec.ts", "on", true),
  );
  await writeFile(
    join(project, "failure.config.ts"),
    config("failure.spec.ts", "retain-on-failure", true),
  );
}

function config(testMatch: string, trace: string, accelerated: boolean): string {
  const module = accelerated ? "@loopguard/playwright/config" : "@playwright/test";
  const symbol = accelerated ? "defineLoopGuardConfig as defineConfig" : "defineConfig";
  return `import { ${symbol} } from ${JSON.stringify(module)};
export default defineConfig({
  testDir: ${JSON.stringify(".")},
  testMatch: ${JSON.stringify(testMatch)},
  workers: 1,
  reporter: [["json", { outputFile: "report.json" }]],
  outputDir: "artifacts",
  use: { trace: ${JSON.stringify(trace)} },
});
`;
}

function brokerEnv(socketPath: string, capability: string): NodeJS.ProcessEnv {
  return {
    LOOPGUARD_BROWSER_SOCKET: socketPath,
    LOOPGUARD_BROWSER_CAPABILITY: capability,
    LOOPGUARD_BROWSER_SESSION: "integration",
    LOOPGUARD_BROWSER_REQUIRED: "1",
  };
}

async function runPlaywright(
  project: string,
  configFile: string,
  additions: NodeJS.ProcessEnv,
): Promise<void> {
  await execute(process.execPath, [playwrightCli, "test", "--config", configFile], {
    cwd: project,
    env: { ...process.env, ...additions },
    timeout: 45_000,
    maxBuffer: 4 * 1024 * 1024,
  });
}

async function filesBelow(root: string): Promise<string[]> {
  const files: string[] = [];
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) files.push(...(await filesBelow(path)));
    else files.push(path);
  }
  return files;
}
