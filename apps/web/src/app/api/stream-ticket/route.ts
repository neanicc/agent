import { timingSafeEqual } from "node:crypto";

import { cookies } from "next/headers";
import type { NextRequest } from "next/server";


const MAX_REQUEST_BYTES = 8_192;
const SESSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type TicketRequest = { session_id: string; after_session_seq: number };
type UpstreamTicket = { ticket: string; expires_at: string };

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<Response> {
  const cookieStore = await cookies();
  const bearer = cookieStore.get("__Host-loopguard_session")?.value;
  if (!bearer) {
    return problem(401, "authentication_required", "Sign in before opening a session stream.");
  }

  if (!equalSecret(request.headers.get("x-csrf-token"), cookieStore.get("loopguard_csrf")?.value)) {
    return problem(403, "csrf_required", "Refresh the page before opening a session stream.");
  }

  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(declaredLength) || declaredLength > MAX_REQUEST_BYTES) {
    return problem(413, "body_too_large", "The stream-ticket request is too large.");
  }

  const rawBody = await request.text();
  if (new TextEncoder().encode(rawBody).byteLength > MAX_REQUEST_BYTES) {
    return problem(413, "body_too_large", "The stream-ticket request is too large.");
  }
  const body = parseTicketRequest(rawBody);
  if (body === null) {
    return problem(400, "invalid_stream_request", "Provide a session UUID and a non-negative cursor.");
  }

  const apiBase = configuredBase(process.env.CONTROL_API_URL, "http:", "https:");
  if (apiBase === null) {
    return problem(503, "control_api_unconfigured", "The control API connection is not configured.");
  }

  const requestId = `bff_${crypto.randomUUID()}`;
  let upstream: Response;
  try {
    upstream = await fetch(new URL("v1/stream-tickets", apiBase).toString(), {
      method: "POST",
      body: JSON.stringify(body),
      cache: "no-store",
      headers: {
        Accept: "application/json, application/problem+json",
        Authorization: `Bearer ${bearer}`,
        "Content-Type": "application/json",
        Origin: request.nextUrl.origin,
        "X-Request-ID": requestId,
      },
      redirect: "manual",
      signal: request.signal,
    });
  } catch {
    return problem(502, "control_api_unavailable", "The control API could not issue a stream ticket.", requestId);
  }

  if (!upstream.ok) {
    return passthroughProblem(upstream, requestId);
  }

  const issued = await readTicket(upstream);
  if (issued === null) {
    return problem(502, "invalid_stream_ticket", "The control API returned an invalid stream ticket.", requestId);
  }

  const streamBase = streamBaseUrl(apiBase);
  if (streamBase === null) {
    return problem(503, "control_stream_unconfigured", "The session stream connection is not configured.", requestId);
  }
  const streamUrl = new URL(`v1/sessions/${encodeURIComponent(body.session_id)}/stream`, streamBase);
  streamUrl.searchParams.set("ticket", issued.ticket);

  return Response.json(
    { stream_url: streamUrl.toString(), expires_at: issued.expires_at },
    { status: 201, headers: { "Cache-Control": "no-store", "X-Request-ID": requestId } },
  );
}

function parseTicketRequest(raw: string): TicketRequest | null {
  try {
    const value = JSON.parse(raw) as Partial<TicketRequest>;
    if (
      !SESSION_ID.test(value.session_id ?? "") ||
      !Number.isSafeInteger(value.after_session_seq) ||
      Number(value.after_session_seq) < 0
    ) {
      return null;
    }
    return { session_id: value.session_id!, after_session_seq: value.after_session_seq! };
  } catch {
    return null;
  }
}

async function readTicket(response: Response): Promise<UpstreamTicket | null> {
  try {
    const value = (await response.json()) as Partial<UpstreamTicket>;
    const expiresAt = new Date(value.expires_at ?? "");
    if (!value.ticket || value.ticket.length > 1_024 || Number.isNaN(expiresAt.valueOf())) return null;
    return { ticket: value.ticket, expires_at: value.expires_at! };
  } catch {
    return null;
  }
}

function streamBaseUrl(apiBase: URL): URL | null {
  const configured = process.env.CONTROL_STREAM_URL;
  if (configured) return configuredBase(configured, "ws:", "wss:");
  const value = new URL(apiBase);
  value.protocol = value.protocol === "https:" ? "wss:" : "ws:";
  return value;
}

function configuredBase(value: string | undefined, ...protocols: string[]): URL | null {
  if (!value) {
    if (process.env.NODE_ENV === "production") return null;
    value = "http://127.0.0.1:8000/";
  }
  try {
    const url = new URL(value);
    if (!protocols.includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
    url.pathname = url.pathname.endsWith("/") ? url.pathname : `${url.pathname}/`;
    return url;
  } catch {
    return null;
  }
}

function equalSecret(left: string | null, right: string | undefined): boolean {
  if (!left || !right) return false;
  const leftBytes = Buffer.from(left);
  const rightBytes = Buffer.from(right);
  return leftBytes.length === rightBytes.length && timingSafeEqual(leftBytes, rightBytes);
}

async function passthroughProblem(response: Response, requestId: string): Promise<Response> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/problem+json")) {
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": "application/problem+json",
        "X-Request-ID": response.headers.get("x-request-id") ?? requestId,
      },
    });
  }
  return problem(response.status, "stream_ticket_rejected", "The control API rejected the stream ticket request.", requestId);
}

function problem(status: number, code: string, detail: string, requestId = `bff_${crypto.randomUUID()}`): Response {
  return Response.json(
    {
      type: `https://docs.loopguard.dev/reference/control-api-errors#${code}`,
      code,
      title: "Session stream request failed",
      detail,
      request_id: requestId,
      retryable: status >= 500,
    },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": "application/problem+json",
        "X-Request-ID": requestId,
      },
    },
  );
}
