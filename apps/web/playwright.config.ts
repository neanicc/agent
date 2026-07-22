import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "line",
  use: {
    baseURL: "http://127.0.0.1:3113",
    colorScheme: "light",
    extraHTTPHeaders: { "X-LoopGuard-E2E-Auth": "loopguard-playwright-fixture" },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3113",
    env: {
      ...process.env,
      LOOPGUARD_E2E_AUTH_TOKEN: "loopguard-playwright-fixture",
      LOOPGUARD_SESSION_SECRET: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    },
    url: "http://127.0.0.1:3113/inbox",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
