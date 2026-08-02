import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

type TestRecord = {
  file: string;
  projects: string[];
  tags: string[];
  dependencies: string[];
  routes: string[];
  components: string[];
  imports: string[];
};

type Mapping = { test: string; values: string[] };
type Config = {
  smoke_projects: string[];
  shared_paths: string[];
  config_paths: string[];
  ignored_paths: string[];
  routes: Array<{ route: string; tests: string[] }>;
  components: Array<{ path: string; tests: string[] }>;
  dependencies: Mapping[];
  imports: Mapping[];
};

const MAX_OUTPUT_BYTES = 16 * 1024 * 1024;

export function generateManifest(repository: string, configText: string, discovery: unknown) {
  const root = resolve(repository);
  const config = parseConfig(configText);
  const tests = discoverTests(root, discovery);
  const byFile = new Map(tests.map((test) => [test.file, test]));
  for (const mapping of config.routes) {
    for (const file of mapping.tests) requireTest(byFile, file).routes.push(mapping.route);
  }
  for (const mapping of config.components) {
    for (const file of mapping.tests) requireTest(byFile, file).components.push(mapping.path);
  }
  for (const mapping of config.dependencies) {
    requireTest(byFile, mapping.test).dependencies.push(...mapping.values);
  }
  for (const mapping of config.imports) {
    requireTest(byFile, mapping.test).imports.push(...mapping.values);
  }
  for (const test of tests) {
    test.projects = unique(test.projects);
    test.tags = unique(test.tags);
    test.dependencies = unique(test.dependencies.map(safePath));
    test.routes = unique(test.routes.map(routePath));
    test.components = unique(test.components.map(safePath));
    test.imports = unique(test.imports.map(safePath));
    for (const dependency of test.dependencies) requireTest(byFile, dependency);
  }
  return {
    schema_version: 1,
    generated_by: discoveryVersion(discovery),
    tests: tests.sort((a, b) => a.file.localeCompare(b.file)),
    smoke_projects: unique(config.smoke_projects),
    shared_paths: unique(config.shared_paths.map(safePattern)),
    config_paths: unique(config.config_paths.map(safePattern)),
    ignored_paths: unique(config.ignored_paths.map(safePattern)),
  };
}

function discoverTests(root: string, discovery: unknown): TestRecord[] {
  if (!isObject(discovery)) throw new Error("Playwright discovery output must be an object");
  const records = new Map<string, TestRecord>();
  const walk = (suite: unknown) => {
    if (!isObject(suite)) return;
    const suiteFile = typeof suite.file === "string" ? repositoryPath(root, suite.file) : undefined;
    if (Array.isArray(suite.specs)) {
      for (const spec of suite.specs) {
        if (!isObject(spec)) continue;
        const file =
          typeof spec.file === "string" ? repositoryPath(root, spec.file) : suiteFile;
        if (file === undefined) continue;
        const record = records.get(file) ?? {
          file,
          projects: [],
          tags: [],
          dependencies: [],
          routes: [],
          components: [],
          imports: [],
        };
        if (Array.isArray(spec.tags)) {
          record.tags.push(...spec.tags.filter((tag): tag is string => typeof tag === "string"));
        }
        if (Array.isArray(spec.tests)) {
          for (const test of spec.tests) {
            if (isObject(test) && typeof test.projectName === "string") {
              record.projects.push(test.projectName);
            }
          }
        }
        records.set(file, record);
      }
    }
    if (Array.isArray(suite.suites)) suite.suites.forEach(walk);
  };
  if (Array.isArray(discovery.suites)) discovery.suites.forEach(walk);
  return [...records.values()];
}

function parseConfig(text: string): Config {
  const config: Config = {
    smoke_projects: ["smoke"],
    shared_paths: [],
    config_paths: ["playwright.config.ts", "playwright.config.js"],
    ignored_paths: ["docs/**", "**/*.md"],
    routes: [],
    components: [],
    dependencies: [],
    imports: [],
  };
  let table: { kind: "routes" | "components" | "dependencies" | "imports"; values: Record<string, unknown> } | undefined;
  const flush = () => {
    if (table === undefined) return;
    const values = table.values;
    if (table.kind === "routes") {
      config.routes.push({ route: stringValue(values.route), tests: arrayValue(values.tests) });
    } else if (table.kind === "components") {
      config.components.push({ path: safePath(stringValue(values.path)), tests: arrayValue(values.tests).map(safePath) });
    } else {
      const listKey = table.kind === "dependencies" ? "depends_on" : "paths";
      config[table.kind].push({
        test: safePath(stringValue(values.test)),
        values: arrayValue(values[listKey]).map(safePath),
      });
    }
    table = undefined;
  };
  for (const [index, raw] of text.split(/\r?\n/).entries()) {
    const line = stripComment(raw).trim();
    if (!line) continue;
    const heading = /^\[\[(routes|components|dependencies|imports)\]\]$/.exec(line);
    if (heading) {
      flush();
      table = { kind: heading[1] as "routes" | "components" | "dependencies" | "imports", values: {} };
      continue;
    }
    const assignment = /^([a-z_]+)\s*=\s*(.+)$/.exec(line);
    if (!assignment) throw new Error(`unsupported browser.toml syntax at line ${index + 1}`);
    const key = assignment[1]!;
    const value = parseTomlValue(assignment[2]!);
    if (table !== undefined) {
      if (Object.hasOwn(table.values, key)) throw new Error(`duplicate browser.toml key ${key}`);
      table.values[key] = value;
    } else if (key === "smoke_projects" || key === "shared_paths" || key === "config_paths" || key === "ignored_paths") {
      config[key] = arrayValue(value);
    } else if (key !== "schema_version" || value !== 1) {
      throw new Error(`unsupported browser.toml key ${key}`);
    }
  }
  flush();
  if (config.smoke_projects.length === 0) throw new Error("browser.toml requires a smoke project");
  return config;
}

function parseTomlValue(value: string): unknown {
  if (/^"(?:[^"\\]|\\.)*"$/.test(value) || /^\[(?:.|\s)*\]$/.test(value)) {
    try {
      const parsed = JSON.parse(value);
      if (typeof parsed === "string" || (Array.isArray(parsed) && parsed.every((item) => typeof item === "string"))) return parsed;
    } catch {}
  }
  if (/^\d+$/.test(value)) return Number(value);
  throw new Error("browser.toml supports only bounded JSON-style strings and string arrays");
}

function stripComment(line: string): string {
  let quoted = false;
  let escaped = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index]!;
    if (escaped) escaped = false;
    else if (character === "\\") escaped = true;
    else if (character === '"') quoted = !quoted;
    else if (character === "#" && !quoted) return line.slice(0, index);
  }
  return line;
}

function repositoryPath(root: string, file: string): string {
  const absolute = isAbsolute(file) ? resolve(file) : resolve(root, file);
  const path = relative(root, absolute).replaceAll("\\", "/");
  return safePath(path);
}

function safePath(value: string): string {
  const normalized = value.trim().replaceAll("\\", "/");
  if (!normalized || normalized.startsWith("/") || normalized.split("/").some((part) => !part || part === "." || part === "..") || normalized.includes("\0") || normalized.length > 1_024) {
    throw new Error("manifest path is unsafe");
  }
  return normalized;
}

function safePattern(value: string): string {
  const normalized = value.trim().replaceAll("\\", "/");
  if (!normalized || normalized.startsWith("/") || normalized.split("/").includes("..") || normalized.includes("\0") || normalized.length > 1_024) throw new Error("manifest pattern is unsafe");
  return normalized;
}

function routePath(value: string): string {
  const normalized = value.trim().replace(/\/$/, "") || "/";
  if (!normalized.startsWith("/") || normalized.includes("?") || normalized.includes("#") || normalized.length > 1_024) throw new Error("manifest route is invalid");
  return normalized;
}

function requireTest(tests: Map<string, TestRecord>, path: string): TestRecord {
  const test = tests.get(safePath(path));
  if (test === undefined) throw new Error(`browser mapping references undiscovered test ${basename(path)}`);
  return test;
}

function unique(values: string[]): string[] {
  return [...new Set(values)].sort();
}

function arrayValue(value: unknown): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string") || value.length > 10_000) throw new Error("browser.toml value must be a bounded string array");
  return value;
}

function stringValue(value: unknown): string {
  if (typeof value !== "string" || !value || value.length > 1_024) throw new Error("browser.toml value must be a bounded string");
  return value;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function discoveryVersion(discovery: unknown): string {
  if (isObject(discovery) && isObject(discovery.config) && typeof discovery.config.version === "string") return `playwright-${discovery.config.version}`;
  return "playwright-cli";
}

function main() {
  const args = new Map<string, string>();
  for (let index = 2; index < process.argv.length; index += 2) {
    const key = process.argv[index];
    const value = process.argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error("usage: playwright_manifest --repo DIR --output FILE [--config FILE]");
    args.set(key, value);
  }
  const root = resolve(args.get("--repo") ?? process.cwd());
  const output = resolve(args.get("--output") ?? join(root, ".loopguard", "playwright-manifest.json"));
  const configPath = resolve(args.get("--config") ?? join(root, ".loopguard", "browser.toml"));
  const config = readFileSync(configPath, "utf8");
  const executable = process.platform === "win32" ? "npx.cmd" : "npx";
  const discovered = spawnSync(executable, ["--no-install", "playwright", "test", "--list", "--reporter=json"], {
    cwd: root,
    encoding: "utf8",
    timeout: 60_000,
    maxBuffer: MAX_OUTPUT_BYTES,
    env: { ...process.env, CI: "1" },
    shell: false,
  });
  if (discovered.status !== 0) throw new Error("Playwright test discovery failed");
  const start = discovered.stdout.indexOf("{");
  if (start < 0 || Buffer.byteLength(discovered.stdout) > MAX_OUTPUT_BYTES) throw new Error("Playwright discovery output is invalid");
  const manifest = generateManifest(root, config, JSON.parse(discovered.stdout.slice(start)));
  const body = JSON.stringify(manifest, null, 2) + "\n";
  mkdirSync(dirname(output), { recursive: true, mode: 0o700 });
  const temporary = join(dirname(output), `.${basename(output)}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`);
  writeFileSync(temporary, body, { encoding: "utf8", mode: 0o600, flag: "wx" });
  renameSync(temporary, output);
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  try {
    main();
  } catch (error) {
    process.stderr.write(`playwright_manifest_failed: ${error instanceof Error ? error.message : "unknown error"}\n`);
    process.exitCode = 1;
  }
}
