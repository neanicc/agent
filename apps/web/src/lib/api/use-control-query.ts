"use client";

import { useQuery } from "@tanstack/react-query";

import { BrowserControlClient } from "./browser-client";


let browserClient: BrowserControlClient | undefined;

export function controlClient(): BrowserControlClient {
  browserClient ??= new BrowserControlClient({ csrfToken: readCsrfToken });
  return browserClient;
}

export function useControlQuery<T>(path: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["control", path],
    queryFn: ({ signal }) => controlClient().request<T>(path, { signal }),
    enabled: options.enabled,
  });
}

function readCsrfToken(): string {
  if (typeof document === "undefined") return "";
  return document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "";
}
