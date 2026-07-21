import assert from "node:assert/strict";
import test from "node:test";
import { generateManifest } from "./playwright_manifest.js";

const discovery = {
  config: { version: "1.61.1" },
  suites: [
    {
      file: "/repo/e2e/login.spec.ts",
      specs: [
        {
          tags: ["@auth"],
          tests: [{ projectName: "chromium" }, { projectName: "webkit" }],
        },
      ],
    },
    {
      file: "/repo/e2e/auth.setup.ts",
      specs: [{ tags: ["@setup"], tests: [{ projectName: "setup" }] }],
    },
  ],
};

test("combines Playwright discovery with strict browser mappings", () => {
  const manifest = generateManifest(
    "/repo",
    `
schema_version = 1
smoke_projects = ["smoke"]
shared_paths = ["src/theme.ts"]
[[routes]]
route = "/login"
tests = ["e2e/login.spec.ts"]
[[dependencies]]
test = "e2e/login.spec.ts"
depends_on = ["e2e/auth.setup.ts"]
`,
    discovery,
  );
  assert.equal(manifest.generated_by, "playwright-1.61.1");
  assert.deepEqual(manifest.tests[1], {
    file: "e2e/login.spec.ts",
    projects: ["chromium", "webkit"],
    tags: ["@auth"],
    dependencies: ["e2e/auth.setup.ts"],
    routes: ["/login"],
    components: [],
    imports: [],
  });
});

test("rejects mappings to undiscovered or unsafe tests", () => {
  assert.throws(() =>
    generateManifest(
      "/repo",
      '[[routes]]\nroute="/admin"\ntests=["../escape.spec.ts"]',
      discovery,
    ),
  );
  assert.throws(() =>
    generateManifest(
      "/repo",
      '[[routes]]\nroute="/admin"\ntests=["e2e/missing.spec.ts"]',
      discovery,
    ),
  );
});
