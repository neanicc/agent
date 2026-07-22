import { timingSafeEqual } from "node:crypto";

import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

import { CSRF_COOKIE, SESSION_COOKIE, TRANSACTION_COOKIE } from "@/auth";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<Response> {
  const cookieStore = await cookies();
  if (!equalSecret(request.headers.get("x-csrf-token"), cookieStore.get(CSRF_COOKIE)?.value)) {
    return Response.json(
      {
        type: "https://docs.loopguard.dev/reference/control-api-errors#csrf_required",
        code: "csrf_required",
        title: "Sign-out request rejected",
        detail: "Refresh the page before signing out.",
        request_id: `bff_${crypto.randomUUID()}`,
        retryable: false,
      },
      { status: 403, headers: { "Content-Type": "application/problem+json" } },
    );
  }
  cookieStore.delete(SESSION_COOKIE);
  cookieStore.delete(TRANSACTION_COOKIE);
  cookieStore.delete(CSRF_COOKIE);
  return new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } });
}

function equalSecret(left: string | null, right: string | undefined): boolean {
  if (!left || !right) return false;
  const lhs = Buffer.from(left);
  const rhs = Buffer.from(right);
  return lhs.length === rhs.length && timingSafeEqual(lhs, rhs);
}
