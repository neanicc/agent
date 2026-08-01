import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const PUBLIC_SECTIONS = [
  "getting-started",
  "tutorials",
  "how-to",
  "product",
  "reference",
  "security",
  "privacy",
  "operations",
  "migrations",
];
const docsRoot = path.resolve(process.cwd(), "../../docs");
const output = path.resolve(process.cwd(), "src/generated/docs-index.json");
const documents = [];

for (const section of PUBLIC_SECTIONS) {
  const root = path.join(docsRoot, section);
  for (const file of await markdownFiles(root)) {
    const source = await readFile(file, "utf8");
    const relative = path.relative(docsRoot, file).split(path.sep).join("/");
    const slug = relative.replace(/\.md$/, "");
    const title = source.match(/^#\s+(.+)$/m)?.[1]?.trim() ?? titleCase(path.basename(slug));
    const description =
      source
        .split(/\n\s*\n/)
        .map((value) => value.replace(/^#+\s+/gm, "").replace(/\s+/g, " ").trim())
        .find((value) => value && value !== title && !value.startsWith("```")) ?? "";
    documents.push({
      slug,
      section,
      title,
      description: description.slice(0, 240),
      source,
    });
  }
}

documents.sort((left, right) => left.slug.localeCompare(right.slug));
await mkdir(path.dirname(output), { recursive: true });
await writeFile(output, `${JSON.stringify({ schemaVersion: 1, documents }, null, 2)}\n`);

async function markdownFiles(root) {
  const entries = await readdir(root, { withFileTypes: true }).catch((error) => {
    if (error?.code === "ENOENT") return [];
    throw error;
  });
  const files = [];
  for (const entry of entries) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) files.push(...(await markdownFiles(target)));
    if (entry.isFile() && entry.name.endsWith(".md")) files.push(target);
  }
  return files;
}

function titleCase(value) {
  return value
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}
