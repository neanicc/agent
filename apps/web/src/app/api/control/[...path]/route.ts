import { timingSafeEqual } from "node:crypto";

import { cookies } from "next/headers";
import type { NextRequest } from "next/server";


const MAX_BROWSER_BODY_BYTES = 1_048_576;
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const FORWARDED_RESPONSE_HEADERS = new Set(["cache-control", "content-type", "x-request-id"]);

type RouteContext = { params: Promise<{ path: string[] }> };

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function POST(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function PUT(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const method = request.method.toUpperCase();
  const path = await safePath(context.params);
  if (path === null) {
    return localProblem(400, "invalid_proxy_path", "The requested API path is invalid.");
  }

  const cookieStore = await cookies();
  const bearer = cookieStore.get("__Host-loopguard_session")?.value;
  if (!bearer) {
    return localProblem(401, "authentication_required", "Sign in before calling the control API.");
  }

  if (!SAFE_METHODS.has(method)) {
    const csrfHeader = request.headers.get("x-csrf-token");
    const csrfCookie = cookieStore.get("loopguard_csrf")?.value;
    if (!equalSecret(csrfHeader, csrfCookie)) {
      return localProblem(403, "csrf_required", "Refresh the page and try the action again.");
    }
  }

  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(contentLength) || contentLength > MAX_BROWSER_BODY_BYTES) {
    return localProblem(413, "body_too_large", "The request body exceeds the 1 MiB browser limit.");
  }

  const apiBase = controlApiBase();
  if (apiBase instanceof Response) {
    return apiBase;
  }
  const upstream = new URL(path, apiBase);
  upstream.search = request.nextUrl.search;
  const body = SAFE_METHODS.has(method) ? undefined : await request.arrayBuffer();
  if (body !== undefined && body.byteLength > MAX_BROWSER_BODY_BYTES) {
    return localProblem(413, "body_too_large", "The request body exceeds the 1 MiB browser limit.");
  }

  const headers = new Headers({
    Accept: "application/json, application/problem+json",
    Authorization: `Bearer ${bearer}`,
  });
  copyHeader(request.headers, headers, "content-type");
  copyHeader(request.headers, headers, "idempotency-key");
  copyHeader(request.headers, headers, "x-request-id");
  copyHeader(request.headers, headers, "x-csrf-token");

  try {
    const response = await fetch(upstream, {
      method,
      body,
      cache: "no-store",
      headers,
      redirect: "manual",
      signal: request.signal,
    });
    const responseHeaders = new Headers();
    for (const [name, value] of response.headers) {
      if (FORWARDED_RESPONSE_HEADERS.has(name.toLowerCase())) {
        responseHeaders.set(name, value);
      }
    }
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch {
    return localProblem(502, "control_api_unavailable", "The control API could not be reached. Try again.");
  }
}

async function safePath(params: RouteContext["params"]): Promise<string | null> {
  const { path } = await params;
  if (path.length < 2 || path[0] !== "v1") {
    return null;
  }
  if (path.some((segment) => !segment || segment === "." || segment === ".." || segment.includes("/"))) {
    return null;
  }
  return `${path.map(encodeURIComponent).join("/")}`;
}

function controlApiBase(): URL | Response {
  const configured = process.env.CONTROL_API_URL;
  if (!configured) {
    if (process.env.NODE_ENV === "production") {
      return localProblem(503, "control_api_unconfigured", "The control API connection is not configured.");
    }
    return new URL("http://127.0.0.1:8000/");
  }
  try {
    const url = new URL(configured);
    if (!new Set(["http:", "https:"]).has(url.protocol) || url.username || url.password) {
      throw new TypeError();
    }
    url.pathname = url.pathname.endsWith("/") ? url.pathname : `${url.pathname}/`;
    return url;
  } catch {
    return localProblem(503, "control_api_unconfigured", "The control API connection is invalid.");
  }
}

function equalSecret(left: string | null, right: string | undefined): boolean {
  if (!left || !right) {
    return false;
  }
  const leftBytes = Buffer.from(left);
  const rightBytes = Buffer.from(right);
  return leftBytes.length === rightBytes.length && timingSafeEqual(leftBytes, rightBytes);
}

function copyHeader(source: Headers, destination: Headers, name: string): void {
  const value = source.get(name);
  if (value !== null) {
    destination.set(name, value);
  }
}

function localProblem(status: number, code: string, detail: string): Response {
  return Response.json(
    {
      type: `https://docs.loopguard.dev/reference/control-api-errors#${code}`,
      code,
      title: "Web control request failed",
      detail,
      request_id: `bff_${crypto.randomUUID()}`,
      retryable: status >= 500,
      doc_url: `https://docs.loopguard.dev/reference/control-api-errors#${code}`,
    },
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}
