// @vitest-environment node

import { createHash } from "node:crypto";

import { describe, expect, test, vi } from "vitest";

import {
  completeCallback,
  createAuthorizationRequest,
  refreshBrowserSession,
  routeFor,
  sealSession,
  type OIDCConfig,
} from "./auth";

const config: OIDCConfig = {
  issuer: "https://identity.loopguard.test",
  authorizationEndpoint: "https://identity.loopguard.test/authorize",
  tokenEndpoint: "https://identity.loopguard.test/token",
  jwksUri: "https://identity.loopguard.test/.well-known/jwks.json",
  clientId: "loopguard-web",
  redirectUri: "https://loopguard.test/auth/callback",
  scopes: ["openid", "profile", "offline_access"],
};

describe("OIDC trust chain", () => {
  test("uses a fresh high-entropy S256 PKCE challenge", async () => {
    const first = await createAuthorizationRequest(config, "/runs");
    const second = await createAuthorizationRequest(config, "/runs");

    expect(first.transaction.codeVerifier).not.toBe(second.transaction.codeVerifier);
    expect(Buffer.from(first.transaction.codeVerifier, "base64url")).toHaveLength(32);
    expect(first.url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(first.url.searchParams.get("code_challenge")).toBe(
      createHash("sha256").update(first.transaction.codeVerifier).digest("base64url"),
    );
  });

  test("callback rejects an OIDC state mismatch before token exchange", async () => {
    const { transaction } = await createAuthorizationRequest(config, "/inbox");
    const exchange = vi.fn();
    const result = await completeCallback(
      { code: "authorization-code", state: "substituted" },
      transaction,
      config,
      { exchange },
    );

    expect(result).toMatchObject({ ok: false, code: "invalid_state" });
    expect(exchange).not.toHaveBeenCalled();
  });

  test("authorization errors still require the original OIDC state", async () => {
    const { transaction } = await createAuthorizationRequest(config, "/inbox");
    const result = await completeCallback(
      { error: "access_denied", state: "substituted" },
      transaction,
      config,
    );

    expect(result).toMatchObject({ ok: false, code: "invalid_state" });
  });

  test("callback rejects an ID-token nonce mismatch", async () => {
    const { transaction } = await createAuthorizationRequest(config, "/inbox");
    const result = await completeCallback(
      { code: "authorization-code", state: transaction.state },
      transaction,
      config,
      {
        exchange: async () => ({ access_token: "access", id_token: "id-token", expires_in: 300 }),
        verifyIDToken: async () => ({ sub: "user-1", nonce: "substituted" }),
      },
    );

    expect(result).toMatchObject({ ok: false, code: "invalid_nonce" });
  });

  test("multi-audience ID token rejects a different authorized party", async () => {
    const { transaction } = await createAuthorizationRequest(config, "/inbox");
    const result = await completeCallback(
      { code: "authorization-code", state: transaction.state },
      transaction,
      config,
      {
        exchange: async () => ({ access_token: "access", id_token: "id-token", expires_in: 300 }),
        verifyIDToken: async () => ({
          sub: "user-1",
          nonce: transaction.nonce,
          aud: [config.clientId, "another-api"],
          azp: "another-client",
        }),
      },
    );

    expect(result).toMatchObject({ ok: false, code: "invalid_authorized_party" });
  });

  test("protected route redirects an unauthenticated request", async () => {
    const result = await routeFor(new Request("https://loopguard.test/runs/active"), {
      sessionSecret: testSecret(),
    });

    expect(result.status).toBe(307);
    expect(new URL(result.headers.get("location")!).pathname).toBe("/sign-in");
  });

  test("production never accepts the explicit E2E authentication header", async () => {
    const result = await routeFor(
      new Request("https://loopguard.test/runs", {
        headers: { "x-loopguard-e2e-auth": "fixture-token" },
      }),
      {
        sessionSecret: testSecret(),
        environment: { NODE_ENV: "production", LOOPGUARD_E2E_AUTH_TOKEN: "fixture-token" },
      },
    );

    expect(result.status).toBe(307);
  });

  test("protected route accepts a valid encrypted session", async () => {
    const secret = testSecret();
    const cookie = await sealSession(
      {
        accessToken: "access",
        expiresAt: Math.floor(Date.now() / 1_000) + 300,
        issuer: config.issuer,
        subject: "user-1",
      },
      secret,
    );
    const result = await routeFor(
      new Request("https://loopguard.test/runs/active", {
        headers: { cookie: `__Host-loopguard_session=${cookie}` },
      }),
      { sessionSecret: secret },
    );

    expect(result.status).toBe(200);
  });

  test("expired access token rotates inside the encrypted server session", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      Response.json({
        access_token: "renewed-access",
        refresh_token: "rotated-refresh",
        expires_in: 600,
        token_type: "Bearer",
      }),
    );
    vi.stubGlobal("fetch", fetcher);

    const refreshed = await refreshBrowserSession(
      {
        accessToken: "expired-access",
        refreshToken: "refresh",
        expiresAt: 100,
        issuer: config.issuer,
        subject: "user-1",
      },
      config,
      200,
    );

    expect(refreshed).toMatchObject({
      accessToken: "renewed-access",
      refreshToken: "rotated-refresh",
      expiresAt: 800,
    });
    expect(fetcher).toHaveBeenCalledWith(
      config.tokenEndpoint,
      expect.objectContaining({ body: expect.any(URLSearchParams), redirect: "manual" }),
    );
  });
});

function testSecret(): string {
  return Buffer.alloc(32, 7).toString("base64url");
}
