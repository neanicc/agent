# Demo script

## Part 1: no guard
Run:
```bash
loopguard demo --mode no-guard
```
Narration: “Here’s a broken repo-inspection agent. It keeps calling the same tool with slightly different paths. Nothing crashes, but it’s burning tokens every step.”
Expected output includes ten repeated `read_file(...package.json...)` attempts with rising cost.

## Part 2: guarded
Run:
```bash
loopguard demo
```
Expected output:
```text
LoopGuard tripped
Semantic loop detected across last 3 tool calls
Similarity: 0.91
Tool: read_file
[t] terminate  [c] continue once  [a] allowlist  [i] inject correction
```
Inject: `Stop looking for package.json. Inspect pyproject.toml instead.`
Expected recovery: the next call reads `pyproject.toml` and succeeds.

## Part 3: optional Cerebras
Run:
```bash
loopguard demo --scenario cerebras
```
Narration: “This uses Cerebras for a live LLM/tool-calling loop demo, but the guard itself is local and provider-independent.”
