# Browser performance and correctness

LoopGuard treats faster browser verification as an evidence-gated optimization. Impacted-test
selection never replaces the completion or pull-request gates, and browser reuse remains disabled
for a repository until its own benchmark proves a material improvement without an isolation,
reporter, trace, or selection-correctness regression.

## What is measured

Keep these distributions separate:

- `cold_start_ms`: first broker/browser startup on the pinned version.
- `warm_context_ms`: fresh isolated context creation on an already-running broker.
- `impacted_suite_ms`: the conservative selected test slice.
- `full_suite_ms`: the repository's complete Playwright gate.

Every report includes sample count, p50, p95, mean, standard deviation, selected/total tests, cache
hits, retries, trace bytes, full-gate runs, and selection escapes. An escape means a later full gate
found a failure that the impacted run missed. A latency result with zero recorded full gates has a
`null` escape rate and is not enablement evidence.

## Reproducible benchmark protocol

1. Pin the same Node, Playwright, browser revision, machine class, project, and test data for both
   modes. Do not compare against launching a new browser for every test; Playwright already reuses
   its browser inside a worker.
2. Run the unmodified native Playwright configuration and the opt-in LoopGuard fixture as separate
   CLI invocations. Measure cold first run, repeated native invocation, repeated broker invocation,
   impacted selection, full suite, and broker-unavailable native fallback.
3. Discard at least five warm-up samples, then retain at least 30 samples for every distribution.
   Publish p50/p95 and variance, never the best run.
4. Exercise cookie/storage isolation, standard fixtures, reporters, traces, cancellation, failing
   test cleanup, and fallback. Record every regression as a counter rather than excluding the run.
5. Run the full gate after impacted verification and record whether it exposes a selection escape.

`BrowserBenchmarkPolicy` reports cross-invocation reuse savings separately from selection savings.
Its default requires at least a 20% improvement at both p50 and p95, 30 post-warm-up samples, 30
full-gate correctness observations, no isolation/reporter/trace regressions, and no increase in the
selection-escape rate. Otherwise its decision remains disabled with a machine-readable reason.

## Current product posture

The package ships the adapter as explicit opt-in infrastructure, not a universal speed claim.
`loopguard doctor --json` reports whether both the config adapter and fixture imports are present.
Repositories should retain native mode until their pinned local/CI benchmark produces an enabled
decision. If the broker is unavailable, the fixture uses native Playwright unless
`LOOPGUARD_BROWSER_REQUIRED=1` was deliberately set by a controlled gate.
