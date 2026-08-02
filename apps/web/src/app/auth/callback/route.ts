import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

import {
  completeCallback,
  CSRF_COOKIE,
  oidcConfigFromEnvironment,
  randomBase64URL,
  sealSession,
  secureCookieOptions,
  SESSION_COOKIE,
  TRANSACTION_COOKIE,
  unsealTransaction,
  webSessionTTLSeconds,
} from "@/auth";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<Response> {
  const cookieStore = await cookies();
  try {
    const transaction = await unsealTransaction(cookieStore.get(TRANSACTION_COOKIE)?.value);
    if (!transaction) return failedRedirect(request, "invalid_transaction");
    cookieStore.delete(TRANSACTION_COOKIE);
    const result = await completeCallback(
      {
        code: request.nextUrl.searchParams.get("code"),
        state: request.nextUrl.searchParams.get("state"),
        error: request.nextUrl.searchParams.get("error"),
      },
      transaction,
      oidcConfigFromEnvironment(),
      { signal: request.signal },
    );
    if (!result.ok) return failedRedirect(request, result.code);

    const session = await sealSession(result.session);
    cookieStore.delete(SESSION_COOKIE);
    cookieStore.set(
      SESSION_COOKIE,
      session,
      secureCookieOptions(webSessionTTLSeconds()),
    );
    cookieStore.set(CSRF_COOKIE, randomBase64URL(32), secureCookieOptions(8 * 60 * 60, false));
    return Response.redirect(new URL(result.returnTo, request.url), 303);
  } catch {
    cookieStore.delete(TRANSACTION_COOKIE);
    return failedRedirect(request, "callback_failed");
  }
}

function failedRedirect(request: NextRequest, code: string): Response {
  const signIn = new URL("/sign-in", request.url);
  signIn.searchParams.set("error", code);
  return Response.redirect(signIn, 303);
}
