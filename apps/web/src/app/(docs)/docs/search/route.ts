import { NextRequest, NextResponse } from "next/server";

import { docs } from "@/lib/docs";


export function GET(request: NextRequest) {
  const query = request.nextUrl.searchParams.get("q")?.trim().toLowerCase() ?? "";
  if (query.length < 2 || query.length > 128) {
    return NextResponse.json({ query, results: [] });
  }
  const terms = query.split(/\s+/).filter(Boolean).slice(0, 8);
  const results = docs
    .map((document) => {
      const haystack = `${document.title}\n${document.description}\n${document.source}`.toLowerCase();
      const score = terms.reduce(
        (total, term) => total + (document.title.toLowerCase().includes(term) ? 10 : 0) + occurrences(haystack, term),
        0,
      );
      return { document, score, position: Math.max(0, haystack.indexOf(terms[0])) };
    })
    .filter((value) => value.score > 0)
    .sort((left, right) => right.score - left.score || left.document.title.localeCompare(right.document.title))
    .slice(0, 12)
    .map(({ document, position }) => ({
      slug: document.slug,
      title: document.title,
      section: document.section,
      snippet: snippet(document.source, position),
    }));
  return NextResponse.json(
    { query, results },
    { headers: { "Cache-Control": "public, max-age=60, stale-while-revalidate=300" } },
  );
}

function occurrences(value: string, term: string): number {
  let count = 0;
  let cursor = 0;
  while ((cursor = value.indexOf(term, cursor)) !== -1 && count < 20) {
    count += 1;
    cursor += term.length;
  }
  return count;
}

function snippet(value: string, position: number): string {
  const plain = value
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#*`>|()_-]+/g, " ")
    .replace(/[\[\]]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const start = Math.max(0, Math.min(plain.length, position) - 70);
  const content = plain.slice(start, start + 220);
  return `${start > 0 ? "…" : ""}${content}${start + content.length < plain.length ? "…" : ""}`;
}
