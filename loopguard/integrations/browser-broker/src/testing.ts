import { readFile, writeFile } from "node:fs/promises";
import type { BrowserContextHandle, BrowserProcess } from "./broker.js";

export class FakeContext implements BrowserContextHandle {
  readonly options: Record<string, unknown>;
  readonly pages: FakePage[] = [];
  readonly cookiesByName = new Map<string, string>();
  closed = false;
  routeHandler?: (url: string) => Promise<void>;

  constructor(options: Record<string, unknown>) {
    this.options = options;
  }

  async addCookies(cookies: Array<{ name: string; value: string }>) {
    for (const cookie of cookies) this.cookiesByName.set(cookie.name, cookie.value);
  }

  async cookies() {
    return [...this.cookiesByName].map(([name, value]) => ({ name, value }));
  }

  async newPage() {
    const page = new FakePage(this);
    this.pages.push(page);
    return page;
  }

  async route(_pattern: string, handler: (route: unknown) => Promise<void>) {
    this.routeHandler = async (url) => {
      let aborted = false;
      await handler({
        request: () => ({ url: () => url }),
        abort: async () => {
          aborted = true;
        },
        continue: async () => undefined,
      });
      if (aborted) throw new Error("navigation_denied");
    };
  }

  async close() {
    this.closed = true;
    for (const page of this.pages) page.closed = true;
  }

  isClosed() {
    return this.closed;
  }
}

export class FakePage {
  closed = false;
  uploadedPath?: string;
  constructor(readonly context: FakeContext) {}

  async goto(url: string) {
    await this.context.routeHandler?.(url);
    return null;
  }

  async click(_selector: string) {}
  async fill(_selector: string, _value: string) {}
  async screenshot(options: { path: string }) {
    await writeFile(options.path, "png");
  }
  async setInputFiles(_selector: string, path: string) {
    this.uploadedPath = path;
  }
  async waitForEvent(event: "download") {
    if (event !== "download") throw new Error("unsupported event");
    return { saveAs: async (path: string) => writeFile(path, "download") };
  }
  async close() {
    this.closed = true;
  }
}

export class FakeBrowser implements BrowserProcess {
  readonly contexts: FakeContext[] = [];
  connected = true;
  disconnected?: () => void;
  storageStateObserved?: string;

  async newContext(options: Record<string, unknown>) {
    if (typeof options.storageState === "string") {
      this.storageStateObserved = await readFile(options.storageState, "utf8");
    }
    const context = new FakeContext(options);
    this.contexts.push(context);
    return context;
  }

  version() {
    return "fake-1.0";
  }

  isConnected() {
    return this.connected;
  }

  on(event: "disconnected", handler: () => void) {
    if (event === "disconnected") this.disconnected = handler;
  }

  endpoint() {
    return "ws://fake.invalid/playwright";
  }

  crash() {
    this.connected = false;
    this.disconnected?.();
  }

  async close() {
    this.connected = false;
    for (const context of this.contexts) await context.close();
  }
}

export class FakeLauncher {
  starts = 0;
  current?: FakeBrowser;
  async launch() {
    this.starts += 1;
    this.current = new FakeBrowser();
    return this.current;
  }
}
