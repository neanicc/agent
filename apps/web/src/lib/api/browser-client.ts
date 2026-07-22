import { decodeResponse } from "./errors";
import type { components } from "./generated";

type Fetcher = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

type BrowserClientOptions = {
  fetcher?: Fetcher;
  csrfToken: () => string;
};

export type SignedActionAcceptance = components["schemas"]["SignedActionAcceptance"];

export type ActionAcceptance = {
  action_id: string;
  state: string;
  executed_at?: string | null;
};

export class BrowserControlClient {
  readonly #fetcher: Fetcher;
  readonly #csrfToken: BrowserClientOptions["csrfToken"];

  constructor(options: BrowserClientOptions) {
    this.#fetcher = options.fetcher ?? fetch;
    this.#csrfToken = options.csrfToken;
  }

  sessions(options: { signal?: AbortSignal; after?: string } = {}) {
    const query = options.after ? `?page_cursor=${encodeURIComponent(options.after)}` : "";
    return this.request<{ items: Array<Record<string, unknown>>; next_cursor: string | null }>(
      `/v1/sessions${query}`,
      { signal: options.signal },
    );
  }

  createAction(action: SignedActionAcceptance, signal?: AbortSignal) {
    return this.request<ActionAcceptance>("/v1/actions", {
      method: "POST",
      body: JSON.stringify(action),
      signal,
    });
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    if (!path.startsWith("/v1/") && path !== "/v1/actions") {
      throw new TypeError("Browser control requests must target a versioned API path");
    }
    const method = (init.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = {
      Accept: "application/json, application/problem+json",
      ...headersToRecord(init.headers),
    };
    if (!new Set(["GET", "HEAD", "OPTIONS"]).has(method)) {
      const csrf = this.#csrfToken();
      if (!csrf) {
        throw new TypeError("A CSRF token is required for state-changing requests");
      }
      headers["X-CSRF-Token"] = csrf;
    }
    if (init.body !== undefined && headers["Content-Type"] === undefined) {
      headers["Content-Type"] = "application/json";
    }
    delete headers.Authorization;
    delete headers.authorization;
    const response = await this.#fetcher(`/api/control${path}`, {
      ...init,
      credentials: "same-origin",
      headers,
      redirect: "manual",
    });
    return decodeResponse<T>(response);
  }
}

function headersToRecord(headers: HeadersInit | undefined): Record<string, string> {
  return headers === undefined ? {} : Object.fromEntries(new Headers(headers).entries());
}
