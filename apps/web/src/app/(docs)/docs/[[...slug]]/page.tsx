import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { DocsSearch } from "@/components/docs-search";
import {
  defaultDocSlug,
  docsNavigation,
  markdownHeadings,
  readDoc,
  renderMarkdown,
} from "@/lib/docs";


type PageProps = { params: Promise<{ slug?: string[] }> };

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const value = await params;
  const slug = value.slug?.join("/") || defaultDocSlug;
  const document = readDoc(slug);
  if (!document) return {};
  const site = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "") || "https://docs.loopguard.dev";
  return {
    title: document.title,
    description: document.description,
    alternates: { canonical: `${site}/docs/${document.slug}` },
  };
}

export default async function DocsPage({ params }: PageProps) {
  const value = await params;
  const slug = value.slug?.join("/") || defaultDocSlug;
  const document = readDoc(slug);
  if (!document) notFound();
  const navigation = docsNavigation();
  const headings = markdownHeadings(document.source);
  const repository = process.env.NEXT_PUBLIC_REPOSITORY_URL || "https://github.com/neanicc/agent";
  const version = process.env.BUILD_SHA?.slice(0, 12) || "development";

  return (
    <div className="docs-shell">
      <a className="skip-link" href="#documentation">Skip to documentation</a>
      <header className="docs-header">
        <Link className="docs-brand" href="/docs">LoopGuard <span>Docs</span></Link>
        <span className="docs-version" title={`Build ${version}`}>v0.1 · {version}</span>
        <DocsSearch />
        <Link className="docs-console-link" href="/inbox">Open console</Link>
      </header>
      <div className="docs-layout">
        <nav aria-label="Documentation" className="docs-navigation">
          {navigation.map((group) => (
            <section key={group.section}>
              <h2>{group.label}</h2>
              <ul>
                {group.documents.map((item) => (
                  <li key={item.slug}>
                    <Link aria-current={item.slug === document.slug ? "page" : undefined} href={`/docs/${item.slug}`}>
                      {item.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </nav>
        <main className="docs-main" id="documentation">
          <div className="docs-breadcrumb">
            <span>{document.section.replace("-", " ")}</span>
            <span aria-hidden="true">/</span>
            <span>{document.title}</span>
          </div>
          <article className="docs-article">{renderMarkdown(document.source, document.slug)}</article>
          <footer className="docs-page-footer">
            <p>Found a gap? Help make this page sharper.</p>
            <a href={`${repository}/edit/main/docs/${document.slug}.md`}>Edit this page on GitHub</a>
            <span>Documentation build {version}</span>
          </footer>
        </main>
        <aside className="docs-toc">
          <h2>On this page</h2>
          <ol>
            {headings.map((heading) => (
              <li data-depth={heading.depth} key={heading.id}>
                <a href={`#${heading.id}`}>{heading.text}</a>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </div>
  );
}
