import { expect, test } from "@playwright/test";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";

const sourceRoot = resolve(process.cwd(), "src");
const tokenFile = resolve(sourceRoot, "styles/tokens.css");
const sourceFiles = walk(sourceRoot).filter((path) => [".css", ".ts", ".tsx"].includes(extname(path)));

test("raw colours live only in the token file", () => {
  const violations = sourceFiles
    .filter((path) => path !== tokenFile)
    .flatMap((path) => matches(path, /#[\da-f]{3,8}\b|(?:rgb|hsl|oklch|lab|lch)\(/gi));

  expect(violations).toEqual([]);
});

test("source rejects decorative and ambiguous interface shortcuts", () => {
  const forbidden = [
    { name: "decorative gradients", pattern: /(?:linear|radial|conic)-gradient\(/gi },
    { name: "decorative blur", pattern: /(?:backdrop-)?filter\s*:\s*[^;]*blur\(/gi },
    { name: "generic three-column feature grids", pattern: /grid-template-columns\s*:\s*repeat\(\s*3\s*,/gi },
    { name: "generic card wrappers", pattern: /className=["'][^"']*\bcard\b/gi },
    { name: "content glass", pattern: /className=["'][^"']*\bglass\b/gi },
    { name: "emoji controls", pattern: /<button[^>]*>[^<]*[\p{Extended_Pictographic}]/giu },
  ];
  const violations = sourceFiles.flatMap((path) =>
    forbidden.flatMap(({ name, pattern }) =>
      matches(path, pattern).map((match) => ({ ...match, rule: name })),
    ),
  );

  expect(violations).toEqual([]);
});

test("inputs do not use placeholders as their only accessible label", () => {
  const violations = sourceFiles
    .filter((path) => [".tsx", ".ts"].includes(extname(path)))
    .flatMap((path) => {
      const source = readFileSync(path, "utf8");
      const inputs = source.match(/<(?:input|select|textarea)\b[\s\S]*?>/g) ?? [];
      return inputs
        .filter((input) => /placeholder=/.test(input))
        .filter((input) => !/(?:aria-label|aria-labelledby|id)=/.test(input))
        .map((input) => ({ file: relative(process.cwd(), path), input }));
    });

  expect(violations).toEqual([]);
});

function walk(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

function matches(path: string, pattern: RegExp) {
  const source = readFileSync(path, "utf8");
  return [...source.matchAll(pattern)].map((match) => ({
    file: relative(process.cwd(), path),
    line: source.slice(0, match.index).split("\n").length,
    value: match[0],
  }));
}
