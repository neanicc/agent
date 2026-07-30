import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";


const roots = [
  "node_modules/@redocly/openapi-core",
  "node_modules/openapi-typescript/node_modules/@redocly/openapi-core",
];
const vulnerableAdapter = `const DEFAULT_SCHEMA_WITHOUT_TIMESTAMP = js_yaml_1.JSON_SCHEMA.extend({
    implicit: [js_yaml_1.types.merge],
    explicit: [js_yaml_1.types.binary, js_yaml_1.types.omap, js_yaml_1.types.pairs, js_yaml_1.types.set],
});`;
const safeAdapter = `// LoopGuard compatibility patch: js-yaml 5 replaces Schema.extend/types with withTags/tag exports.
const DEFAULT_SCHEMA_WITHOUT_TIMESTAMP = js_yaml_1.CORE_SCHEMA.withTags(
    js_yaml_1.mergeTag,
    js_yaml_1.binaryTag,
    js_yaml_1.omapTag,
    js_yaml_1.pairsTag,
    js_yaml_1.setTag,
);`;

let patched = false;
for (const root of roots) {
  const packagePath = resolve(root, "package.json");
  let metadata;
  try {
    metadata = JSON.parse(await readFile(packagePath, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") continue;
    throw error;
  }
  if (metadata.version !== "1.34.17") {
    throw new Error(`Refusing to patch unexpected @redocly/openapi-core ${metadata.version}`);
  }

  const adapterPath = resolve(root, "lib/js-yaml/index.js");
  const source = await readFile(adapterPath, "utf8");
  if (source.includes(safeAdapter)) {
    patched = true;
    continue;
  }
  if (!source.includes(vulnerableAdapter)) {
    throw new Error("Refusing to patch an unknown Redocly js-yaml adapter");
  }
  await writeFile(adapterPath, source.replace(vulnerableAdapter, safeAdapter));
  patched = true;
}

if (!patched) {
  throw new Error("The pinned Redocly js-yaml adapter was not installed");
}

// minimatch 3/5 expect brace-expansion's historical callable CommonJS export. The patched
// brace-expansion 5 release exposes a bounded `expand` function instead. Keep the old consumers
// on their declared minimatch versions and adapt only that import shape.
const minimatchRoots = [
  "node_modules/eslint-config-next/node_modules/minimatch",
  "node_modules/@eslint/eslintrc/node_modules/minimatch",
  "node_modules/@eslint/config-array/node_modules/minimatch",
  "node_modules/eslint/node_modules/minimatch",
  "node_modules/openapi-typescript/node_modules/minimatch",
];
let minimatchPatches = 0;
for (const root of minimatchRoots) {
  const packagePath = resolve(root, "package.json");
  let metadata;
  try {
    metadata = JSON.parse(await readFile(packagePath, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") continue;
    throw error;
  }
  if (!["3.1.5", "5.1.9"].includes(metadata.version)) {
    throw new Error(`Refusing to patch unexpected minimatch ${metadata.version}`);
  }

  const entryPath = resolve(root, "minimatch.js");
  const source = await readFile(entryPath, "utf8");
  const vulnerableImport = metadata.version === "3.1.5"
    ? "var expand = require('brace-expansion')"
    : "const expand = require('brace-expansion')";
  const safeImport = `${vulnerableImport}.expand`;
  if (source.includes(safeImport)) {
    minimatchPatches += 1;
    continue;
  }
  if (!source.includes(vulnerableImport)) {
    throw new Error(`Refusing to patch an unknown minimatch ${metadata.version} entry`);
  }
  await writeFile(entryPath, source.replace(vulnerableImport, safeImport));
  minimatchPatches += 1;
}

if (minimatchPatches !== minimatchRoots.length) {
  throw new Error(`Expected ${minimatchRoots.length} minimatch compatibility patches, found ${minimatchPatches}`);
}
