import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

import {
  createAuthorizationRequest,
  oidcConfigFromEnvironment,
  sealTransaction,
  secureCookieOptions,
  TRANSACTION_COOKIE,
} from "@/auth";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<Response> {
  try {
    const config = oidcConfigFromEnvironment();
    const { url, transaction } = await createAuthorizationRequest(
      config,
      request.nextUrl.searchParams.get("return_to") ?? "/inbox",
    );
    const sealed = await sealTransaction(transaction);
    (await cookies()).set(TRANSACTION_COOKIE, sealed, secureCookieOptions(10 * 60));
    return Response.redirect(url, 302);
  } catch {
    const signIn = new URL("/sign-in", request.url);
    signIn.searchParams.set("error", "configuration");
    return Response.redirect(signIn, 302);
  }
}
