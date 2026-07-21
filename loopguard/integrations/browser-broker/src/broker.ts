import { randomBytes } from "node:crypto";
import { promises as dns } from "node:dns";
import { lstat, mkdir } from "node:fs/promises";
import { isIP } from "node:net";
import { basename, join, resolve } from "node:path";
import ipaddr from "ipaddr.js";
import type { Browser, BrowserContext, BrowserType } from "playwright";
import type { CreateContextParams, PageAction } from "./protocol.js";
import { PlaintextStorageLeaseManager } from "./storage.js";

export type BrowserName = "chromium" | "firefox" | "webkit";

export interface PageHandle {
  goto(url: string, options?: { waitUntil?: "commit" | "domcontentloaded" | "load" | "networkidle" }): Promise<unknown>;
  click(selector: string): Promise<unknown>;
  fill(selector: string, value: string): Promise<unknown>;
  screenshot(options: { path: string; fullPage?: boolean }): Promise<unknown>;
  setInputFiles(selector: string, path: string): Promise<unknown>;
  waitForEvent(event: "download"): Promise<{ saveAs(path: string): Promise<unknown> }>;
  close(): Promise<unknown>;
}

export interface BrowserContextHandle {
  newPage(): Promise<PageHandle>;
  close(): Promise<unknown>;
  route(pattern: string, handler: (route: unknown) => Promise<void>): Promise<unknown>;
  addCookies?(cookies: Array<{ name: string; value: string }>): Promise<unknown>;
  cookies?(): Promise<Array<{ name: string; value: string }>>;
  isClosed?(): boolean;
  readonly options?: Record<string, unknown>;
}

export interface BrowserProcess {
  newContext(options: Record<string, unknown>): Promise<BrowserContextHandle>;
  close(): Promise<unknown>;
  version(): string;
  isConnected(): boolean;
  on(event: "disconnected", handler: () => void): void;
}

export interface BrowserLauncher {
  launch(browser: BrowserName): Promise<BrowserProcess>;
}

export type ResolveHost = (hostname: string) => Promise<string[]>;

type BrokerOptions = {
  stateDirectory: string;
  launcher?: BrowserLauncher;
  resolveHost?: ResolveHost;
  idleTtlMs?: number;
  now?: () => number;
  allowPrivateNetworks?: boolean;
  storageManager?: PlaintextStorageLeaseManager;
};

type ContextLease = {
  id: string;
  sessionId: string;
  browser: BrowserName;
  context: BrowserContextHandle;
  artifactDirectory: string;
  policy: OriginPolicy;
  lastUsedAt: number;
};

export type PublicContextLease = Pick<
  ContextLease,
  "id" | "sessionId" | "browser" | "context" | "artifactDirectory"
>;

export class BrowserBroker {
  readonly stateDirectory: string;
  readonly artifactRoot: string;
  readonly #launcher: BrowserLauncher;
  readonly #resolveHost: ResolveHost;
  readonly #idleTtlMs: number;
  readonly #now: () => number;
  readonly #allowPrivateNetworks: boolean;
  readonly #storageManager: PlaintextStorageLeaseManager;
  readonly #browsers = new Map<BrowserName, BrowserProcess>();
  readonly #contexts = new Map<string, ContextLease>();
  readonly #sessionContexts = new Map<string, string>();
  readonly #disconnectTimers = new Map<string, NodeJS.Timeout>();
  readonly #idleTimer: NodeJS.Timeout;
  #closed = false;

  private constructor(options: Required<Omit<BrokerOptions, "launcher" | "resolveHost" | "storageManager">> & {
    launcher: BrowserLauncher;
    resolveHost: ResolveHost;
    storageManager: PlaintextStorageLeaseManager;
  }) {
    this.stateDirectory = options.stateDirectory;
    this.artifactRoot = join(options.stateDirectory, "artifacts");
    this.#launcher = options.launcher;
    this.#resolveHost = options.resolveHost;
    this.#idleTtlMs = options.idleTtlMs;
    this.#now = options.now;
    this.#allowPrivateNetworks = options.allowPrivateNetworks;
    this.#storageManager = options.storageManager;
    this.#idleTimer = setInterval(
      () => void this.sweepIdle(),
      Math.max(1_000, Math.min(Math.floor(options.idleTtlMs / 2), 60_000)),
    );
    this.#idleTimer.unref();
  }

  static async start(options: BrokerOptions): Promise<BrowserBroker> {
    const stateDirectory = resolve(options.stateDirectory);
    await ensureOwnerDirectory(stateDirectory);
    await ensureOwnerDirectory(join(stateDirectory, "artifacts"));
    const storageManager =
      options.storageManager ??
      (await PlaintextStorageLeaseManager.open(join(stateDirectory, "auth-plaintext")));
    return new BrowserBroker({
      stateDirectory,
      launcher: options.launcher ?? new PlaywrightLauncher(),
      resolveHost: options.resolveHost ?? resolveAddresses,
      idleTtlMs: options.idleTtlMs ?? 15 * 60_000,
      now: options.now ?? Date.now,
      allowPrivateNetworks: options.allowPrivateNetworks ?? false,
      storageManager,
    });
  }

  async createContext(params: CreateContextParams): Promise<PublicContextLease> {
    this.#assertOpen();
    this.#cancelDisconnect(params.sessionId);
    if (this.#sessionContexts.has(params.sessionId)) {
      throw new Error("session already owns a browser context");
    }
    const browser = await this.#browser(params.browser);
    const id = randomBytes(32).toString("base64url");
    const artifactDirectory = join(this.artifactRoot, randomBytes(24).toString("hex"));
    await ensureOwnerDirectory(artifactDirectory);
    const policy = new OriginPolicy({
      allowedOrigins: params.allowedOrigins,
      resolveHost: this.#resolveHost,
      allowPrivateNetworks: this.#allowPrivateNetworks,
    });
    const options: Record<string, unknown> = {
      locale: params.locale ?? "en-US",
      timezoneId: params.timezoneId ?? "UTC",
      serviceWorkers: params.serviceWorkers,
      acceptDownloads: true,
    };
    if (params.storageStatePath !== null) options.storageState = params.storageStatePath;
    const context =
      params.storageStatePath === null
        ? await browser.newContext(options)
        : await this.#storageManager.consume(
            params.storageStatePath,
            async (safePath) =>
              browser.newContext({ ...options, storageState: safePath }),
          );
    try {
      await context.route("**/*", async (candidate) => {
        const route = candidate as {
          request(): { url(): string };
          abort(reason?: string): Promise<unknown>;
          continue(): Promise<unknown>;
        };
        try {
          await policy.assertAllowed(route.request().url());
          await route.continue();
        } catch {
          await route.abort("blockedbyclient");
        }
      });
    } catch (error) {
      await context.close().catch(() => undefined);
      throw error;
    }
    const lease: ContextLease = {
      id,
      sessionId: params.sessionId,
      browser: params.browser,
      context,
      artifactDirectory,
      policy,
      lastUsedAt: this.#now(),
    };
    this.#contexts.set(id, lease);
    this.#sessionContexts.set(params.sessionId, id);
    return lease;
  }

  async closeContext(contextId: string, sessionId: string): Promise<void> {
    const lease = this.#lease(contextId, sessionId);
    await this.#destroy(lease);
  }

  async closeSession(sessionId: string): Promise<void> {
    this.#cancelDisconnect(sessionId);
    const contextId = this.#sessionContexts.get(sessionId);
    if (contextId === undefined) return;
    const lease = this.#contexts.get(contextId);
    if (lease !== undefined) await this.#destroy(lease);
  }

  async runPage(
    contextId: string,
    sessionId: string,
    actions: PageAction[],
  ): Promise<{ artifacts: string[] }> {
    if (actions.length > 256) throw new Error("page action limit exceeded");
    const lease = this.#lease(contextId, sessionId);
    lease.lastUsedAt = this.#now();
    this.#cancelDisconnect(sessionId);
    const page = await lease.context.newPage();
    const artifacts: string[] = [];
    try {
      for (const action of actions) {
        switch (action.type) {
          case "goto":
            await lease.policy.assertAllowed(action.url);
            await page.goto(
              action.url,
              action.waitUntil === undefined ? undefined : { waitUntil: action.waitUntil },
            );
            break;
          case "click":
            await page.click(action.selector);
            break;
          case "fill":
            await page.fill(action.selector, action.value);
            break;
          case "screenshot": {
            const safeName = safeArtifactName(action.name);
            const path = join(lease.artifactDirectory, safeName);
            await page.screenshot({ path, fullPage: action.fullPage ?? false });
            artifacts.push(path);
            break;
          }
          case "upload": {
            const source = join(
              lease.artifactDirectory,
              safeArtifactName(action.artifactName),
            );
            await page.setInputFiles(action.selector, source);
            break;
          }
          case "download": {
            const destination = join(
              lease.artifactDirectory,
              safeArtifactName(action.name),
            );
            const [download] = await Promise.all([
              page.waitForEvent("download"),
              page.click(action.selector),
            ]);
            await download.saveAs(destination);
            artifacts.push(destination);
            break;
          }
        }
      }
      return { artifacts };
    } finally {
      await page.close().catch(() => undefined);
      lease.lastUsedAt = this.#now();
    }
  }

  clientDisconnected(sessionId: string, graceMs = 5_000): void {
    this.#cancelDisconnect(sessionId);
    const timer = setTimeout(() => {
      this.#disconnectTimers.delete(sessionId);
      void this.closeSession(sessionId);
    }, Math.max(0, Math.min(graceMs, 60_000)));
    timer.unref();
    this.#disconnectTimers.set(sessionId, timer);
  }

  async sweepIdle(): Promise<number> {
    const cutoff = this.#now() - this.#idleTtlMs;
    const stale = [...this.#contexts.values()].filter((lease) => lease.lastUsedAt < cutoff);
    for (const lease of stale) await this.#destroy(lease);
    return stale.length;
  }

  health(): { browsers: Record<string, string>; activeContexts: number } {
    return {
      browsers: Object.fromEntries(
        [...this.#browsers].map(([name, browser]) => [name, browser.version()]),
      ),
      activeContexts: this.#contexts.size,
    };
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    clearInterval(this.#idleTimer);
    for (const timer of this.#disconnectTimers.values()) clearTimeout(timer);
    this.#disconnectTimers.clear();
    for (const lease of [...this.#contexts.values()]) await this.#destroy(lease);
    for (const browser of this.#browsers.values()) await browser.close().catch(() => undefined);
    this.#browsers.clear();
    await this.#storageManager.cleanup();
  }

  async #browser(name: BrowserName): Promise<BrowserProcess> {
    const current = this.#browsers.get(name);
    if (current?.isConnected()) return current;
    const launched = await this.#launcher.launch(name);
    launched.on("disconnected", () => void this.#browserDisconnected(name, launched));
    this.#browsers.set(name, launched);
    return launched;
  }

  async #browserDisconnected(name: BrowserName, browser: BrowserProcess): Promise<void> {
    if (this.#browsers.get(name) !== browser) return;
    this.#browsers.delete(name);
    const lost = [...this.#contexts.values()].filter((lease) => lease.browser === name);
    for (const lease of lost) await this.#destroy(lease);
  }

  #lease(contextId: string, sessionId: string): ContextLease {
    const lease = this.#contexts.get(contextId);
    if (lease === undefined) throw new Error("unknown context capability");
    if (lease.sessionId !== sessionId) throw new Error("context session mismatch");
    return lease;
  }

  async #destroy(lease: ContextLease): Promise<void> {
    this.#contexts.delete(lease.id);
    if (this.#sessionContexts.get(lease.sessionId) === lease.id) {
      this.#sessionContexts.delete(lease.sessionId);
    }
    this.#cancelDisconnect(lease.sessionId);
    await lease.context.close().catch(() => undefined);
  }

  #cancelDisconnect(sessionId: string): void {
    const timer = this.#disconnectTimers.get(sessionId);
    if (timer !== undefined) clearTimeout(timer);
    this.#disconnectTimers.delete(sessionId);
  }

  #assertOpen(): void {
    if (this.#closed) throw new Error("browser broker is closed");
  }
}

export class OriginPolicy {
  readonly #origins: Set<string>;
  readonly #resolveHost: ResolveHost;
  readonly #allowPrivateNetworks: boolean;

  constructor(options: {
    allowedOrigins: string[];
    resolveHost: ResolveHost;
    allowPrivateNetworks?: boolean;
  }) {
    this.#origins = new Set(options.allowedOrigins.map((value) => new URL(value).origin));
    this.#resolveHost = options.resolveHost;
    this.#allowPrivateNetworks = options.allowPrivateNetworks ?? false;
  }

  async assertAllowed(value: string): Promise<void> {
    let url: URL;
    try {
      url = new URL(value);
    } catch {
      throw new Error("navigation URL is invalid");
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      throw new Error("navigation scheme is denied");
    }
    if (url.username || url.password) throw new Error("navigation credentials are denied");
    if (!this.#origins.has(url.origin)) throw new Error("navigation origin is not allowed");
    const hostname = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
    if (hostname === "localhost" || hostname.endsWith(".localhost")) {
      throw new Error("navigation network is denied");
    }
    const addresses = isIP(hostname) ? [hostname] : await this.#resolveHost(hostname);
    if (addresses.length === 0) throw new Error("navigation host did not resolve");
    if (!this.#allowPrivateNetworks && addresses.some(isNonPublicAddress)) {
      throw new Error("navigation network is denied");
    }
  }
}

export class PlaywrightLauncher implements BrowserLauncher {
  async launch(browser: BrowserName): Promise<BrowserProcess> {
    const playwright = await import("playwright");
    const type = playwright[browser] as BrowserType<Browser>;
    const instance = await type.launch({ headless: true });
    return new PlaywrightBrowserProcess(instance);
  }
}

class PlaywrightBrowserProcess implements BrowserProcess {
  constructor(readonly browser: Browser) {}
  async newContext(options: Record<string, unknown>): Promise<BrowserContextHandle> {
    return (await this.browser.newContext(options)) as BrowserContext;
  }
  close() { return this.browser.close(); }
  version() { return this.browser.version(); }
  isConnected() { return this.browser.isConnected(); }
  on(event: "disconnected", handler: () => void) { this.browser.on(event, handler); }
}

async function resolveAddresses(hostname: string): Promise<string[]> {
  return (await dns.lookup(hostname, { all: true, verbatim: true })).map((item) => item.address);
}

function isNonPublicAddress(address: string): boolean {
  try {
    const parsed = ipaddr.parse(address.split("%")[0]!);
    const normalized =
      parsed.kind() === "ipv6" && (parsed as ipaddr.IPv6).isIPv4MappedAddress()
        ? (parsed as ipaddr.IPv6).toIPv4Address()
        : parsed;
    return normalized.range() !== "unicast";
  } catch {
    return true;
  }
}

function safeArtifactName(value: string): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value) || basename(value) !== value) {
    throw new Error("artifact name is unsafe");
  }
  return value;
}

async function ensureOwnerDirectory(path: string): Promise<void> {
  await mkdir(path, { recursive: true, mode: 0o700 });
  const status = await lstat(path);
  if (!status.isDirectory() || status.isSymbolicLink()) throw new Error("state directory is unsafe");
  if (
    process.platform !== "win32" &&
    typeof process.getuid === "function" &&
    (status.uid !== process.getuid() || (status.mode & 0o777) !== 0o700)
  ) {
    throw new Error("state directory must be owner-only");
  }
}
