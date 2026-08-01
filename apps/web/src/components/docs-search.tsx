"use client";

import Link from "next/link";
import { FormEvent, KeyboardEvent, useState } from "react";


type SearchResult = {
  slug: string;
  title: string;
  section: string;
  snippet: string;
};

export function DocsSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "error">("idle");

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = query.trim();
    if (value.length < 2) return;
    setState("loading");
    try {
      const response = await fetch(`/docs/search?q=${encodeURIComponent(value)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Search failed");
      const body = (await response.json()) as { results: SearchResult[] };
      setResults(body.results);
      setState("ready");
    } catch {
      setState("error");
    }
  }

  function dismiss(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      setResults([]);
      setState("idle");
    }
  }

  return (
    <div className="docs-search" onKeyDown={dismiss}>
      <form onSubmit={search} role="search">
        <label className="visually-hidden" htmlFor="docs-search-input">
          Search documentation
        </label>
        <input
          id="docs-search-input"
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search docs"
          type="search"
          value={query}
        />
        <button type="submit">Search</button>
      </form>
      <div
        aria-label="Documentation search results"
        aria-live="polite"
        className="docs-search__results"
        role={state === "ready" && results.length > 0 ? "region" : undefined}
      >
        {state === "loading" ? <p>Searching…</p> : null}
        {state === "error" ? <p>Search is temporarily unavailable.</p> : null}
        {state === "ready" && results.length === 0 ? <p>No matching pages.</p> : null}
        {results.length > 0 ? (
          <ul>
            {results.map((result) => (
              <li key={result.slug}>
                <Link href={`/docs/${result.slug}`} onClick={() => setResults([])}>
                  {result.title}
                </Link>
                <span>{result.section.replace("-", " ")}</span>
                <p>{result.snippet}</p>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
