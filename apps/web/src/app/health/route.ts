export const dynamic = "force-dynamic";

const headers = {
  "Cache-Control": "no-store",
  "Content-Type": "application/json",
} as const;

export function GET(): Response {
  const buildSha = process.env.BUILD_SHA?.trim();
  if (!buildSha) {
    return Response.json(
      { status: "degraded", build_sha: "unavailable" },
      { status: 503, headers },
    );
  }
  return Response.json({ status: "ok", build_sha: buildSha }, { status: 200, headers });
}
