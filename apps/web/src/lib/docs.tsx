import "server-only";

import Link from "next/link";
import path from "node:path";
import type { ReactNode } from "react";

import index from "@/generated/docs-index.json";
import { DocsCopyButton } from "@/components/docs-copy-button";


export type Doc = (typeof index.documents)[number];
export type Heading = { depth: number; id: string; text: string };

export const docs = index.documents as Doc[];
export const defaultDocSlug = "getting-started/quickstart";

const sectionLabels: Record<string, string> = {
  "getting-started": "Quickstart",
  tutorials: "Tutorials",
  "how-to": "How-to",
  product: "Concepts",
  reference: "Reference",
  security: "Security & privacy",
  privacy: "Privacy",
  operations: "Operations",
  migrations: "Migration",
};

export function readDoc(slug: string): Doc | undefined {
  return docs.find((document) => document.slug === slug);
}

export function docsNavigation() {
  return Object.entries(sectionLabels)
    .map(([section, label]) => ({
      section,
      label,
      documents: docs.filter((document) => document.section === section),
    }))
    .filter((group) => group.documents.length > 0);
}

export function markdownHeadings(source: string): Heading[] {
  const counts = new Map<string, number>();
  return source
    .split("\n")
    .flatMap((line) => {
      const match = /^(#{2,4})\s+(.+?)\s*$/.exec(line);
      if (!match) return [];
      const base = headingId(match[2]);
      const count = counts.get(base) ?? 0;
      counts.set(base, count + 1);
      return [{ depth: match[1].length, id: count ? `${base}-${count + 1}` : base, text: match[2] }];
    });
}

export function renderMarkdown(source: string, slug: string): ReactNode[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const output: ReactNode[] = [];
  const headingCounts = new Map<string, number>();
  let cursor = 0;
  let key = 0;

  while (cursor < lines.length) {
    const line = lines[cursor];
    if (!line.trim()) {
      cursor += 1;
      continue;
    }

    const fence = /^```([\w+-]*)\s*$/.exec(line);
    if (fence) {
      const language = fence[1] || "text";
      const code: string[] = [];
      cursor += 1;
      while (cursor < lines.length && !lines[cursor].startsWith("```")) {
        code.push(lines[cursor]);
        cursor += 1;
      }
      cursor += 1;
      const value = code.join("\n");
      output.push(
        <div className="docs-code" key={key++}>
          <div>
            <span>{language}</span>
            <DocsCopyButton value={value} />
          </div>
          <pre>
            <code>{value}</code>
          </pre>
        </div>,
      );
      continue;
    }

    const heading = /^(#{1,4})\s+(.+?)\s*$/.exec(line);
    if (heading) {
      const depth = heading[1].length;
      const base = headingId(heading[2]);
      const count = headingCounts.get(base) ?? 0;
      headingCounts.set(base, count + 1);
      const id = count ? `${base}-${count + 1}` : base;
      const content = inline(heading[2], slug, `${key}-heading`);
      if (depth === 1) output.push(<h1 id={id} key={key++}>{content}</h1>);
      if (depth === 2) output.push(<h2 id={id} key={key++}>{content}<Anchor id={id} /></h2>);
      if (depth === 3) output.push(<h3 id={id} key={key++}>{content}<Anchor id={id} /></h3>);
      if (depth === 4) output.push(<h4 id={id} key={key++}>{content}<Anchor id={id} /></h4>);
      cursor += 1;
      continue;
    }

    if (
      cursor + 1 < lines.length
      && line.includes("|")
      && /^\s*\|?[\s:|-]+\|[\s:|-]*\|?\s*$/.test(lines[cursor + 1])
    ) {
      const headers = cells(line);
      cursor += 2;
      const rows: string[][] = [];
      while (cursor < lines.length && lines[cursor].includes("|") && lines[cursor].trim()) {
        rows.push(cells(lines[cursor]));
        cursor += 1;
      }
      output.push(
        <div className="docs-table-scroll" key={key++}>
          <table>
            <thead><tr>{headers.map((value, indexValue) => <th key={indexValue}>{inline(value, slug, `th-${key}-${indexValue}`)}</th>)}</tr></thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {headers.map((_, columnIndex) => <td key={columnIndex}>{inline(row[columnIndex] ?? "", slug, `td-${key}-${rowIndex}-${columnIndex}`)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (cursor < lines.length && /^[-*]\s+/.test(lines[cursor])) {
        items.push(lines[cursor].replace(/^[-*]\s+/, ""));
        cursor += 1;
      }
      output.push(<ul key={key++}>{items.map((item, indexValue) => <li key={indexValue}>{inline(item, slug, `ul-${key}-${indexValue}`)}</li>)}</ul>);
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (cursor < lines.length && /^\d+\.\s+/.test(lines[cursor])) {
        items.push(lines[cursor].replace(/^\d+\.\s+/, ""));
        cursor += 1;
      }
      output.push(<ol key={key++}>{items.map((item, indexValue) => <li key={indexValue}>{inline(item, slug, `ol-${key}-${indexValue}`)}</li>)}</ol>);
      continue;
    }

    if (line.startsWith("> ")) {
      const values: string[] = [];
      while (cursor < lines.length && lines[cursor].startsWith("> ")) {
        values.push(lines[cursor].slice(2));
        cursor += 1;
      }
      output.push(<blockquote key={key++}>{inline(values.join(" "), slug, `quote-${key}`)}</blockquote>);
      continue;
    }

    if (/^---+$/.test(line.trim())) {
      output.push(<hr key={key++} />);
      cursor += 1;
      continue;
    }

    const paragraph = [line.trim()];
    cursor += 1;
    while (
      cursor < lines.length
      && lines[cursor].trim()
      && !/^(#{1,4})\s|^```|^[-*]\s+|^\d+\.\s+|^>\s|^---+$/.test(lines[cursor])
      && !(cursor + 1 < lines.length && lines[cursor].includes("|") && /^\s*\|?[\s:|-]+\|/.test(lines[cursor + 1]))
    ) {
      paragraph.push(lines[cursor].trim());
      cursor += 1;
    }
    output.push(<p key={key++}>{inline(paragraph.join(" "), slug, `p-${key}`)}</p>);
  }
  return output;
}

function inline(value: string, slug: string, keyPrefix: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
  let offset = 0;
  let count = 0;
  for (const match of value.matchAll(pattern)) {
    const indexValue = match.index ?? 0;
    if (indexValue > offset) parts.push(value.slice(offset, indexValue));
    const token = match[0];
    if (token.startsWith("`")) {
      parts.push(<code key={`${keyPrefix}-${count++}`}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      parts.push(<strong key={`${keyPrefix}-${count++}`}>{token.slice(2, -2)}</strong>);
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (link) {
        const href = docHref(link[2], slug);
        parts.push(href ? <Link href={href} key={`${keyPrefix}-${count++}`}>{link[1]}</Link> : link[1]);
      }
    }
    offset = indexValue + token.length;
  }
  if (offset < value.length) parts.push(value.slice(offset));
  return parts;
}

function docHref(value: string, slug: string): string | null {
  const trimmed = value.trim();
  if (/^https?:\/\//.test(trimmed)) return trimmed;
  if (trimmed === "/docs" || trimmed.startsWith("/docs/")) return trimmed;
  if (trimmed.startsWith("#")) return trimmed;
  if (/^[a-z]+:/i.test(trimmed)) return null;
  const [pathname, fragment] = trimmed.split("#", 2);
  const resolved = path.posix
    .normalize(path.posix.join(path.posix.dirname(slug), pathname))
    .replace(/^\.\.\//, "")
    .replace(/\.md$/, "");
  return `/docs/${resolved}${fragment ? `#${fragment}` : ""}`;
}

function headingId(value: string): string {
  return value
    .toLowerCase()
    .replace(/[`*_~]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "section";
}

function cells(line: string): string[] {
  return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((value) => value.trim());
}

function Anchor({ id }: { id: string }) {
  return <a aria-label="Link to this section" className="docs-anchor" href={`#${id}`}>#</a>;
}
