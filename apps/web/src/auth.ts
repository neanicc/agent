import { createRemoteJWKSet, EncryptJWT, jwtDecrypt, jwtVerify, type JWTPayload } from "jose";

export const SESSION_COOKIE = "__Host-loopguard_session";
export const TRANSACTION_COOKIE = "__Host-loopguard_oidc";
export const CSRF_COOKIE = "__Host-loopguard_csrf";

const TRANSACTION_TTL_SECONDS = 10 * 60;
const CLOCK_TOLERANCE_SECONDS = 30;
const ALLOWED_ID_TOKEN_ALGORITHMS = ["RS256", "PS256", "ES256"];

export type OIDCConfig = {
  issuer: string;
  authorizationEndpoint: string;
  tokenEndpoint: string;
  jwksUri: string;
  clientId: string;
  redirectUri: string;
  scopes: string[];
};

export type OIDCTransaction = {
  state: string;
  nonce: string;
  codeVerifier: string;
  returnTo: string;
  createdAt: number;
};

export type BrowserSession = {
  accessToken: string;
  refreshToken?: string;
  expiresAt: number;
  issuer: string;
  subject: string;
};

type TokenResponse = {
  access_token: string;
  refresh_token?: string;
  id_token: string;
  expires_in: number;
  token_type?: string;
};

type CallbackDependencies = {
  exchange?: (input: {
    code: string;
    codeVerifier: string;
    config: OIDCConfig;
    signal?: AbortSignal;
  }) => Promise<TokenResponse>;
  verifyIDToken?: (token: string, config: OIDCConfig) => Promise<JWTPayload>;
  signal?: AbortSignal;
  now?: () => number;
};

export type CallbackResult =
  | { ok: true; session: BrowserSession; returnTo: string }
  | { ok: false; code: string; detail: string };

export async function createAuthorizationRequest(
  config: OIDCConfig,
  returnTo: string,
): Promise<{ url: URL; transaction: OIDCTransaction }> {
  validateConfig(config);
  const transaction: OIDCTransaction = {
    state: randomBase64URL(32),
    nonce: randomBase64URL(32),
    codeVerifier: randomBase64URL(32),
    returnTo: safeReturnTo(returnTo),
    createdAt: Math.floor(Date.now() / 1_000),
  };
  const challenge = base64URL(await crypto.subtle.digest("SHA-256", utf8(transaction.codeVerifier)));
  const url = new URL(config.authorizationEndpoint);
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    scope: config.scopes.join(" "),
    state: transaction.state,
    nonce: transaction.nonce,
    code_challenge: challenge,
    code_challenge_method: "S256",
  }).toString();
  return { url, transaction };
}

export async function completeCallback(
  callback: { code?: string | null; state?: string | null; error?: string | null },
  transaction: OIDCTransaction,
  config: OIDCConfig,
  dependencies: CallbackDependencies = {},
): Promise<CallbackResult> {
  validateConfig(config);
  if (!callback.state || !constantTimeEqual(callback.state, transaction.state)) {
    return failure("invalid_state", "The sign-in state did not match. Start sign-in again.");
  }
  if (callback.error) {
    return failure("authorization_rejected", "The identity provider rejected the authorization request.");
  }
  if (!callback.code) return failure("invalid_callback", "The identity provider omitted the authorization code.");
  const now = Math.floor((dependencies.now?.() ?? Date.now()) / 1_000);
  if (now - transaction.createdAt > TRANSACTION_TTL_SECONDS || transaction.createdAt > now + CLOCK_TOLERANCE_SECONDS) {
    return failure("expired_transaction", "The sign-in request expired. Start sign-in again.");
  }

  let tokens: TokenResponse;
  try {
    tokens = await (dependencies.exchange ?? exchangeAuthorizationCode)({
      code: callback.code,
      codeVerifier: transaction.codeVerifier,
      config,
      signal: dependencies.signal,
    });
  } catch {
    return failure("token_exchange_failed", "The identity provider could not complete sign-in.");
  }
  if (
    !tokens.access_token ||
    !tokens.id_token ||
    !Number.isFinite(tokens.expires_in) ||
    tokens.expires_in <= 0 ||
    (tokens.token_type && tokens.token_type.toLowerCase() !== "bearer")
  ) {
    return failure("invalid_token_response", "The identity provider returned an invalid token response.");
  }

  let claims: JWTPayload;
  try {
    claims = await (dependencies.verifyIDToken ?? verifyIDToken)(tokens.id_token, config);
  } catch {
    return failure("invalid_id_token", "The identity token could not be verified.");
  }
  if (typeof claims.nonce !== "string" || !constantTimeEqual(claims.nonce, transaction.nonce)) {
    return failure("invalid_nonce", "The identity token was not issued for this sign-in request.");
  }
  if (!claims.sub) {
    return failure("invalid_subject", "The identity token did not identify a user.");
  }
  if (Array.isArray(claims.aud) && claims.aud.length > 1 && claims.azp !== config.clientId) {
    return failure("invalid_authorized_party", "The identity token was issued to another client.");
  }

  return {
    ok: true,
    returnTo: transaction.returnTo,
    session: {
      accessToken: tokens.access_token,
      ...(tokens.refresh_token ? { refreshToken: tokens.refresh_token } : {}),
      expiresAt: now + Math.floor(tokens.expires_in),
      issuer: config.issuer,
      subject: claims.sub,
    },
  };
}

export async function sealSession(session: BrowserSession, secret = sessionSecret()): Promise<string> {
  return seal(
    { ...session },
    "loopguard-session",
    Math.floor(Date.now() / 1_000) + webSessionTTLSeconds(),
    secret,
  );
}

export async function unsealSession(
  value: string | undefined,
  secret = sessionSecret(),
  now = Math.floor(Date.now() / 1_000),
): Promise<BrowserSession | null> {
  if (!value) return null;
  try {
    const payload = await unseal(value, "loopguard-session", secret);
    if (
      typeof payload.accessToken !== "string" ||
      typeof payload.expiresAt !== "number" ||
      typeof payload.issuer !== "string" ||
      typeof payload.subject !== "string" ||
      (payload.expiresAt <= now && typeof payload.refreshToken !== "string")
    ) {
      return null;
    }
    return {
      accessToken: payload.accessToken,
      ...(typeof payload.refreshToken === "string" ? { refreshToken: payload.refreshToken } : {}),
      expiresAt: payload.expiresAt,
      issuer: payload.issuer,
      subject: payload.subject,
    };
  } catch {
    return null;
  }
}

export async function refreshBrowserSession(
  session: BrowserSession,
  config = oidcConfigFromEnvironment(),
  now = Math.floor(Date.now() / 1_000),
): Promise<BrowserSession> {
  if (session.expiresAt > now + CLOCK_TOLERANCE_SECONDS) return session;
  if (!session.refreshToken) throw new TypeError("The browser session cannot be refreshed");
  if (session.issuer !== config.issuer) throw new TypeError("The browser session issuer changed");
  const response = await fetch(config.tokenEndpoint, {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "refresh_token",
      client_id: config.clientId,
      refresh_token: session.refreshToken,
    }),
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    redirect: "manual",
  });
  if (!response.ok) throw new TypeError("The identity provider rejected session refresh");
  const tokens = (await response.json()) as Partial<TokenResponse>;
  if (
    !tokens.access_token ||
    !Number.isFinite(tokens.expires_in) ||
    Number(tokens.expires_in) <= 0 ||
    (tokens.token_type && tokens.token_type.toLowerCase() !== "bearer")
  ) {
    throw new TypeError("The identity provider returned an invalid refresh response");
  }
  return {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token ?? session.refreshToken,
    expiresAt: now + Math.floor(Number(tokens.expires_in)),
    issuer: session.issuer,
    subject: session.subject,
  };
}

export async function sealTransaction(transaction: OIDCTransaction, secret = sessionSecret()): Promise<string> {
  return seal(
    { ...transaction },
    "loopguard-oidc-transaction",
    transaction.createdAt + TRANSACTION_TTL_SECONDS,
    secret,
  );
}

export async function unsealTransaction(
  value: string | undefined,
  secret = sessionSecret(),
): Promise<OIDCTransaction | null> {
  if (!value) return null;
  try {
    const payload = await unseal(value, "loopguard-oidc-transaction", secret);
    if (
      typeof payload.state !== "string" ||
      typeof payload.nonce !== "string" ||
      typeof payload.codeVerifier !== "string" ||
      typeof payload.returnTo !== "string" ||
      typeof payload.createdAt !== "number"
    ) {
      return null;
    }
    return {
      state: payload.state,
      nonce: payload.nonce,
      codeVerifier: payload.codeVerifier,
      returnTo: safeReturnTo(payload.returnTo),
      createdAt: payload.createdAt,
    };
  } catch {
    return null;
  }
}

export async function routeFor(
  request: Request,
  options: { sessionSecret?: string; environment?: NodeJS.ProcessEnv } = {},
): Promise<Response> {
  const environment = options.environment ?? process.env;
  const e2eToken = environment.LOOPGUARD_E2E_AUTH_TOKEN;
  if (
    environment.NODE_ENV !== "production" &&
    e2eToken &&
    constantTimeEqual(request.headers.get("x-loopguard-e2e-auth") ?? "", e2eToken)
  ) {
    return new Response(null, { status: 200 });
  }
  const cookie = parseCookie(request.headers.get("cookie"), SESSION_COOKIE);
  const session = await unsealSession(cookie, options.sessionSecret ?? sessionSecret(environment));
  if (session) return new Response(null, { status: 200 });
  const signIn = new URL("/sign-in", request.url);
  signIn.searchParams.set("return_to", safeReturnTo(new URL(request.url).pathname));
  return Response.redirect(signIn, 307);
}

export function oidcConfigFromEnvironment(environment: NodeJS.ProcessEnv = process.env): OIDCConfig {
  const issuer = requiredURL(environment.OIDC_ISSUER, "OIDC_ISSUER");
  const config = {
    issuer: issuer.toString().replace(/\/$/, ""),
    authorizationEndpoint: requiredURL(environment.OIDC_AUTHORIZATION_ENDPOINT, "OIDC_AUTHORIZATION_ENDPOINT").toString(),
    tokenEndpoint: requiredURL(environment.OIDC_TOKEN_ENDPOINT, "OIDC_TOKEN_ENDPOINT").toString(),
    jwksUri: requiredURL(environment.OIDC_JWKS_URI, "OIDC_JWKS_URI").toString(),
    clientId: required(environment.OIDC_CLIENT_ID, "OIDC_CLIENT_ID"),
    redirectUri: requiredURL(environment.OIDC_REDIRECT_URI, "OIDC_REDIRECT_URI").toString(),
    scopes: (environment.OIDC_SCOPES ?? "openid profile email offline_access").split(/\s+/).filter(Boolean),
  } satisfies OIDCConfig;
  validateConfig(config);
  return config;
}

export function sessionSecret(environment: NodeJS.ProcessEnv = process.env): string {
  return required(environment.LOOPGUARD_SESSION_SECRET, "LOOPGUARD_SESSION_SECRET");
}

export function secureCookieOptions(maxAge: number, httpOnly = true) {
  return {
    httpOnly,
    secure: true,
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

export function webSessionTTLSeconds(environment: NodeJS.ProcessEnv = process.env): number {
  const configured = Number(environment.LOOPGUARD_WEB_SESSION_TTL_SECONDS ?? 8 * 60 * 60);
  if (!Number.isSafeInteger(configured) || configured < 300 || configured > 30 * 24 * 60 * 60) {
    throw new TypeError("LOOPGUARD_WEB_SESSION_TTL_SECONDS must be between 300 seconds and 30 days");
  }
  return configured;
}

export function randomBase64URL(bytes = 32): string {
  const value = new Uint8Array(bytes);
  crypto.getRandomValues(value);
  return base64URL(value);
}

async function exchangeAuthorizationCode(input: {
  code: string;
  codeVerifier: string;
  config: OIDCConfig;
  signal?: AbortSignal;
}): Promise<TokenResponse> {
  const response = await fetch(input.config.tokenEndpoint, {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: input.config.clientId,
      redirect_uri: input.config.redirectUri,
      code: input.code,
      code_verifier: input.codeVerifier,
    }),
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    redirect: "manual",
    signal: input.signal,
  });
  if (!response.ok) throw new Error("token exchange rejected");
  return (await response.json()) as TokenResponse;
}

async function verifyIDToken(token: string, config: OIDCConfig): Promise<JWTPayload> {
  const jwks = createRemoteJWKSet(new URL(config.jwksUri));
  const result = await jwtVerify(token, jwks, {
    issuer: config.issuer,
    audience: config.clientId,
    algorithms: ALLOWED_ID_TOKEN_ALGORITHMS,
    clockTolerance: CLOCK_TOLERANCE_SECONDS,
  });
  return result.payload;
}

async function seal(payload: JWTPayload, type: string, expiresAt: number, secret: string): Promise<string> {
  return new EncryptJWT(payload)
    .setProtectedHeader({ alg: "dir", enc: "A256GCM", typ: type })
    .setIssuedAt()
    .setExpirationTime(expiresAt)
    .encrypt(secretKey(secret));
}

async function unseal(value: string, type: string, secret: string): Promise<JWTPayload> {
  const result = await jwtDecrypt(value, secretKey(secret), {
    keyManagementAlgorithms: ["dir"],
    contentEncryptionAlgorithms: ["A256GCM"],
  });
  if (result.protectedHeader.typ !== type) throw new TypeError("unexpected token type");
  return result.payload;
}

function secretKey(secret: string): Uint8Array {
  const value = fromBase64URL(secret);
  if (value.byteLength !== 32) {
    throw new TypeError("LOOPGUARD_SESSION_SECRET must be exactly 32 base64url-encoded bytes");
  }
  return value;
}

function validateConfig(config: OIDCConfig): void {
  if (!config.scopes.includes("openid")) throw new TypeError("OIDC scopes must include openid");
  for (const [name, value] of Object.entries({
    issuer: config.issuer,
    authorizationEndpoint: config.authorizationEndpoint,
    tokenEndpoint: config.tokenEndpoint,
    jwksUri: config.jwksUri,
    redirectUri: config.redirectUri,
  })) {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.hash) {
      throw new TypeError(`${name} must be an HTTPS URL without credentials or fragments`);
    }
  }
  if (!config.clientId.trim()) throw new TypeError("OIDC client ID is required");
}

function required(value: string | undefined, name: string): string {
  if (!value?.trim()) throw new TypeError(`${name} is required`);
  return value;
}

function requiredURL(value: string | undefined, name: string): URL {
  const url = new URL(required(value, name));
  if (url.protocol !== "https:" || url.username || url.password || url.hash) {
    throw new TypeError(`${name} must be an HTTPS URL without credentials or fragments`);
  }
  return url;
}

function failure(code: string, detail: string): CallbackResult {
  return { ok: false, code, detail };
}

function safeReturnTo(value: string): string {
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/inbox";
  return value;
}

function parseCookie(header: string | null, name: string): string | undefined {
  for (const item of header?.split(";") ?? []) {
    const [key, ...parts] = item.trim().split("=");
    if (key === name) return decodeURIComponent(parts.join("="));
  }
  return undefined;
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = utf8(left);
  const rightBytes = utf8(right);
  let mismatch = leftBytes.byteLength ^ rightBytes.byteLength;
  const length = Math.max(leftBytes.byteLength, rightBytes.byteLength);
  for (let index = 0; index < length; index += 1) {
    mismatch |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }
  return mismatch === 0;
}

function utf8(value: string): Uint8Array<ArrayBuffer> {
  return new TextEncoder().encode(value) as Uint8Array<ArrayBuffer>;
}

function base64URL(value: ArrayBuffer | Uint8Array): string {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function fromBase64URL(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new TypeError("invalid base64url value");
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}
