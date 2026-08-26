import { readFile, readdir } from "node:fs/promises";
import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";

const repositoryRoot = path.resolve(process.cwd(), "../..");
const docsRoot = path.join(repositoryRoot, "docs");
const publicRoots = [
  path.join(repositoryRoot, "README.md"),
  path.join(repositoryRoot, "CONTRIBUTING.md"),
  path.join(repositoryRoot, "SECURITY.md"),
  path.join(repositoryRoot, "CODE_OF_CONDUCT.md"),
  ...await markdownFiles(docsRoot, { exclude: new Set(["superpowers"]) }),
];
const files = [...new Set(publicRoots)].sort();
const sources = new Map(await Promise.all(files.map(async (file) => [file, await readFile(file, "utf8")])));
const errors = [];
const external = new Set();

for (const [file, source] of sources) {
  validateFences(file, source);
  validateLinks(file, source);
}

if (process.argv.includes("--external")) {
  await validateExternalLinks();
}

if (errors.length) {
  process.stderr.write(`${errors.map((value) => `- ${value}`).join("\n")}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(
    `Validated ${files.length} public Markdown files, ${external.size} external links, and fenced snippets.\n`,
  );
}

function validateFences(file, source) {
  const fences = [...source.matchAll(/^```([^\n]*)\n([\s\S]*?)^```\s*$/gm)];
  const fenceMarkers = source.match(/^```/gm)?.length ?? 0;
  if (fenceMarkers !== fences.length * 2) errors.push(`${relative(file)} has an unbalanced code fence`);
  for (const [index, match] of fences.entries()) {
    const language = match[1].trim().toLowerCase();
    const body = match[2];
    if (["bash", "sh", "shell"].includes(language)) {
      const result = spawnSync("bash", ["-n"], { input: body, encoding: "utf8" });
      if (result.status !== 0) {
        errors.push(`${relative(file)} shell fence ${index + 1}: ${result.stderr.trim()}`);
      }
    }
    if (language === "json") {
      try {
        JSON.parse(body);
      } catch (error) {
        errors.push(`${relative(file)} JSON fence ${index + 1}: ${error.message}`);
      }
    }
  }
}

function validateLinks(file, source) {
  for (const match of source.matchAll(/\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g)) {
    const raw = match[1].replace(/^<|>$/g, "");
    if (/^https?:\/\//i.test(raw)) {
      if (!isExample(raw)) external.add(raw);
      continue;
    }
    if (/^(?:mailto|tel|data):/i.test(raw)) continue;
    const [targetValue, fragment] = raw.split("#", 2);
    const target = targetValue ? path.resolve(path.dirname(file), decodeURIComponent(targetValue)) : file;
    if (!existsSync(target)) {
      errors.push(`${relative(file)} links to missing public file ${raw}`);
      continue;
    }
    if (statSync(target).isDirectory() || !fragment) continue;
    const targetSource = sources.get(target) ?? readFileSync(target, "utf8");
    if (fragment && !headingAnchors(targetSource).has(fragment.toLowerCase())) {
      errors.push(`${relative(file)} links to missing heading ${raw}`);
    }
  }
}

async function validateExternalLinks() {
  for (const url of [...external].sort()) {
    let response;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        response = await fetch(url, {
          method: attempt === 0 ? "HEAD" : "GET",
          redirect: "follow",
          signal: AbortSignal.timeout(15_000),
          headers: { "User-Agent": "LoopGuard-docs-link-check/1" },
        });
        if (response.ok) break;
      } catch (error) {
        if (attempt === 1) errors.push(`external link ${url}: ${error.message}`);
      }
    }
    if (response && !response.ok) errors.push(`external link ${url}: HTTP ${response.status}`);
  }
}

function headingAnchors(source) {
  const result = new Set();
  const counts = new Map();
  for (const match of source.matchAll(/^#{1,6}\s+(.+?)\s*$/gm)) {
    // Must mirror headingId + duplicate numbering in src/lib/docs.tsx.
    const base =
      match[1]
        .toLowerCase()
        .replace(/[`*_~]/g, "")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "") || "section";
    const count = counts.get(base) ?? 0;
    counts.set(base, count + 1);
    result.add(count ? `${base}-${count + 1}` : base);
  }
  return result;
}

function isExample(value) {
  const host = new URL(value).hostname;
  return host === "example.com" || host.endsWith(".example") || host.endsWith(".test");
}

function relative(file) {
  return path.relative(repositoryRoot, file).split(path.sep).join("/");
}

async function markdownFiles(root, { exclude }) {
  const entries = await readdir(root, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    if (entry.name.startsWith(".") || exclude.has(entry.name)) continue;
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) files.push(...await markdownFiles(target, { exclude }));
    if (entry.isFile() && entry.name.endsWith(".md")) files.push(target);
  }
  return files;
}
