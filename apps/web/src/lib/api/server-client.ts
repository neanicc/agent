import "server-only";

import { randomUUID } from "node:crypto";

import { decodeResponse } from "./errors";

type Fetcher = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

type ServerClientOptions = {
  baseUrl: string;
  token: () => string | Promise<string>;
  fetcher?: Fetcher;
};

export type SessionCollection = {
  items: Array<Record<string, unknown>>;
  next_cursor: string | null;
};

export class ServerControlClient {
  readonly #baseUrl: URL;
  readonly #token: ServerClientOptions["token"];
  readonly #fetcher: Fetcher;

  constructor(options: ServerClientOptions) {
    this.#baseUrl = new URL(options.baseUrl);
    if (!new Set(["http:", "https:"]).has(this.#baseUrl.protocol)) {
      throw new TypeError("Control API base URL must use HTTP or HTTPS");
    }
    if (this.#baseUrl.username || this.#baseUrl.password) {
      throw new TypeError("Control API base URL must not contain credentials");
    }
    this.#token = options.token;
    this.#fetcher = options.fetcher ?? fetch;
  }

  sessions(options: { signal?: AbortSignal; after?: string } = {}): Promise<SessionCollection> {
    const query = options.after ? `?page_cursor=${encodeURIComponent(options.after)}` : "";
    return this.request<SessionCollection>(`/v1/sessions${query}`, { signal: options.signal });
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = await this.#token();
    if (!token) {
      throw new TypeError("A control API bearer token is required");
    }
    const headers: Record<string, string> = {
      Accept: "application/json, application/problem+json",
      Authorization: `Bearer ${token}`,
      "X-Request-ID": `web_${randomUUID()}`,
      ...headersToRecord(init.headers),
    };
    if (init.body !== undefined && headers["Content-Type"] === undefined) {
      headers["Content-Type"] = "application/json";
    }
    const url = new URL(path.replace(/^\//, ""), ensureTrailingSlash(this.#baseUrl));
    const response = await this.#fetcher(url.toString(), {
      ...init,
      cache: "no-store",
      headers,
      redirect: "manual",
    });
    return decodeResponse<T>(response);
  }
}

function ensureTrailingSlash(url: URL): URL {
  const value = new URL(url);
  value.pathname = value.pathname.endsWith("/") ? value.pathname : `${value.pathname}/`;
  return value;
}

function headersToRecord(headers: HeadersInit | undefined): Record<string, string> {
  return headers === undefined ? {} : Object.fromEntries(new Headers(headers).entries());
}
