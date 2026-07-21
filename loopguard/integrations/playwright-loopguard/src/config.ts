import { defineConfig, type PlaywrightTestConfig } from "@playwright/test";

/** Stable marker used by `loopguard doctor` to verify that acceleration is opt-in. */
export const LOOPGUARD_PLAYWRIGHT_ADAPTER = "@loopguard/playwright" as const;

/**
 * Preserve normal Playwright configuration while marking the config as LoopGuard-aware.
 * Projects still import `test` and `expect` from `@loopguard/playwright`.
 */
export function defineLoopGuardConfig(config: PlaywrightTestConfig): PlaywrightTestConfig {
  return defineConfig(config);
}

export type BrokerEnvironment = {
  socketPath: string;
  capability: string;
  sessionId: string;
  allowNativeFallback: boolean;
};

export function brokerEnvironment(
  environment: NodeJS.ProcessEnv = process.env,
): BrokerEnvironment | null {
  const socketPath = environment.LOOPGUARD_BROWSER_SOCKET;
  const capability = environment.LOOPGUARD_BROWSER_CAPABILITY;
  const session = environment.LOOPGUARD_BROWSER_SESSION;
  if (!socketPath || !capability || !session) return null;
  if (socketPath.length > 4_096 || socketPath.includes("\0")) {
    throw new Error("LoopGuard browser socket path is invalid");
  }
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(capability)) {
    throw new Error("LoopGuard browser capability is invalid");
  }
  if (!/^[A-Za-z0-9._:-]{1,192}$/.test(session)) {
    throw new Error("LoopGuard browser session is invalid");
  }
  return {
    socketPath,
    capability,
    sessionId: session,
    allowNativeFallback: environment.LOOPGUARD_BROWSER_REQUIRED !== "1",
  };
}
