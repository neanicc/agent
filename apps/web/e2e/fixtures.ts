import type { Page, Route } from "@playwright/test";

export const ids = {
  session: "42d0ee68-043a-40ba-bc6e-3ddf2238acbf",
  change: "f83ac878-0ec5-4ce8-bbfa-0a3e1c6522bb",
  verification: "2e559f3b-facb-49f6-a277-551f349cab22",
  host: "c2d6438e-1bcd-43fe-b8c3-2adee13f7f0c",
} as const;

type MockOptions = { emptyHosts?: boolean; onRequest?: (route: Route) => void };

export async function mockControlApi(page: Page, options: MockOptions = {}) {
  await page.route(/\/api\/control\/v1\/.*/, async (route) => {
    options.onRequest?.(route);
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/control", "");
    const value = responseFor(path, options);
    await route.fulfill({ json: value, status: 200 });
  });
}

function responseFor(path: string, options: MockOptions): unknown {
  const session = {
    id: ids.session,
    name: "Auth migration",
    repository: "acme/console",
    state: "blocked",
    phase: "verification",
    agent: "Codex",
    model: "gpt-5.6",
    effort: "high",
    cost: "$0.18",
    summary: "A deterministic check regressed after the latest write.",
    updated_at: "2026-07-22T13:40:00Z",
    requires_attention: true,
    severity: "blocking",
    verification: { verdict: "failed", command: "npm test", artifact_id: "proof_42" },
    events: [event(4, "e4", "verification_started")],
  };
  const change = {
    id: ids.change,
    summary: "Harden session validation",
    repository: "acme/console",
    state: "verified",
    actor: "Codex",
    source: "codex-plugin",
    branch: "codex/session-hardening",
    created_at: "2026-07-22T13:32:00Z",
    verification: { verdict: "passed", command: "npm test" },
    diff: "+ validateSession(input)",
  };
  const verification = {
    id: ids.verification,
    name: "Web regression gate",
    verdict: "passed",
    summary: "All deterministic checks passed.",
    command: "npm test",
    exit_status: 0,
    rerun_eligible: true,
    signature: "sha256:proof",
    checks: [{ id: "unit", name: "Unit tests", status: "passed", output: "8 passed" }],
    created_at: "2026-07-22T13:34:00Z",
  };
  const host = {
    id: ids.host,
    name: "Mina’s MacBook",
    state: "ready",
    repository: "acme/console",
    repository_bound: true,
    adapter_version: "1.0.0",
    updated_at: "2026-07-22T13:39:00Z",
    health: { status: "ready", observed_at: "2026-07-22T13:39:00Z", ttl_seconds: 90 },
    integrations: [{ id: "codex", name: "Codex", source: "managed plugin", state: "ready" }],
  };

  if (path === "/v1/sessions") return { items: [session], next_cursor: null };
  if (path === `/v1/sessions/${ids.session}`) return session;
  if (path === "/v1/changes") return { items: [change], next_cursor: null };
  if (path === `/v1/changes/${ids.change}`) return change;
  if (path === "/v1/verifications") return { items: [verification], next_cursor: null };
  if (path === `/v1/verifications/${ids.verification}`) return verification;
  if (path === "/v1/hosts") return { items: options.emptyHosts ? [] : [host], next_cursor: null };
  if (path === `/v1/hosts/${ids.host}`) return host;
  if (path.startsWith("/v1/capabilities")) {
    return { status: "ready", features: { repairs: { available: false, status: "disabled" } } };
  }
  return { items: [], next_cursor: null };
}

export function event(sessionSeq: number, eventId: string, kind: string) {
  return {
    type: "event",
    session_seq: sessionSeq,
    client_stream_seq: sessionSeq,
    event_id: eventId,
    kind,
    occurred_at: "2026-07-22T13:40:00Z",
    payload: {},
  };
}
