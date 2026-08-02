import type { Page, Route } from "@playwright/test";

import repairStates from "../../../contracts/fixtures/repair-states.json";

export const ids = {
  session: "42d0ee68-043a-40ba-bc6e-3ddf2238acbf",
  change: "f83ac878-0ec5-4ce8-bbfa-0a3e1c6522bb",
  verification: "2e559f3b-facb-49f6-a277-551f349cab22",
  host: "c2d6438e-1bcd-43fe-b8c3-2adee13f7f0c",
  device: "1d5512ad-8a62-44bd-a192-f5e082cd9670",
  audit: "4a71c494-7c72-4714-bcab-ef23913fb7d5",
  repair: "018f0000-0000-7000-8000-000000000305",
} as const;

type MockOptions = {
  delayMs?: number;
  emptyHosts?: boolean;
  failPath?: string;
  sessionOverride?: Record<string, unknown>;
  repairReady?: boolean;
  onRequest?: (route: Route) => void;
};

export async function mockControlApi(page: Page, options: MockOptions = {}) {
  await page.route(/\/api\/control\/v1\/.*/, async (route) => {
    options.onRequest?.(route);
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/control", "");
    if (options.delayMs) {
      await new Promise((resolve) => setTimeout(resolve, options.delayMs));
    }
    if (path === options.failPath) {
      await route.fulfill({
        contentType: "application/problem+json",
        json: {
          type: "https://loopguard.dev/problems/upstream-unavailable",
          title: "Control data unavailable",
          status: 503,
          detail: "The authenticated host snapshot could not be loaded.",
          request_id: "req_accessibility_fixture",
        },
        status: 503,
      });
      return;
    }
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
    ...options.sessionOverride,
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
    return {
      status: "ready",
      observed_at: new Date().toISOString(),
      ttl_seconds: 60,
      features: {
        repair: options.repairReady
          ? { available: true, status: "ready", reason: "workflow_registered" }
          : { available: false, status: "unavailable", reason: "workflow_unavailable" },
      },
    };
  }
  if (path === "/v1/repairs") {
    return {
      items: repairStates.map((repair) => ({
        id: repair.id,
        repository_id: repair.repository_id,
        state: repair.state,
        failure_fingerprint: repair.failure_fingerprint,
        created_at: repair.created_at,
        updated_at: repair.updated_at,
        winning_candidate_id: repair.winning_candidate_id,
      })),
      next_cursor: null,
      capability: { available: true, reason: "workflow_registered" },
    };
  }
  if (path.startsWith("/v1/repairs/")) {
    const id = path.slice("/v1/repairs/".length);
    return repairStates.find((repair) => repair.id === id) ?? {};
  }
  if (path === "/v1/preferences") {
    return {
      profile_version: 4,
      source_manifest_hash: "sha256:managed-defaults-v4",
      rules: [
        {
          id: "wcag-contrast",
          severity: "block",
          managed: true,
          source: "organization",
          precedence: "managed minimum",
          affected_capabilities: ["verification", "repair publication"],
        },
        {
          id: "loop-threshold",
          severity: "warn",
          managed: false,
          source: "profile",
          precedence: "profile override",
          affected_capabilities: ["loop intervention"],
        },
      ],
    };
  }
  if (path === "/v1/costs") {
    return {
      window: "30d",
      currency: "USD",
      observed: {
        agent: "1.20",
        judge: "0.04",
        verification: "0.00",
        critic: "0.01",
        repair: "0.30",
      },
      estimated_avoided_cost: null,
    };
  }
  if (path === "/v1/devices") {
    return {
      items: [
        {
          id: ids.device,
          name: "Alice’s iPhone",
          algorithm: "P-256",
          key_id: "dk_alice",
          created_at: "2026-07-20T13:39:00Z",
          last_seen_at: "2026-07-22T13:39:00Z",
          revoked_at: null,
        },
      ],
    };
  }
  if (path === "/v1/audit") {
    return {
      items: [
        {
          id: ids.audit,
          action: "policy_updated",
          target_kind: "preference_profile",
          target_id: "profile_4",
          result: "accepted",
          actor: "Alice",
          request_id: "req_tenant_a",
          created_at: "2026-07-22T13:39:00Z",
        },
      ],
      next_cursor: null,
    };
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
