import { NextResponse, type NextRequest } from "next/server";

import { routeFor } from "@/auth";

export async function proxy(request: NextRequest): Promise<NextResponse> {
  const decision = await routeFor(request);
  if (decision.status === 200) return NextResponse.next();
  return NextResponse.redirect(decision.headers.get("location") ?? new URL("/sign-in", request.url), 307);
}

export const config = {
  matcher: [
    "/inbox/:path*",
    "/runs/:path*",
    "/changes/:path*",
    "/verification/:path*",
    "/hosts/:path*",
    "/policies/:path*",
    "/costs/:path*",
    "/devices/:path*",
    "/audit/:path*",
    "/repairs/:path*",
  ],
};
