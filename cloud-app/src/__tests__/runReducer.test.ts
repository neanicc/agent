import { initialRunState, runReducer } from "../runReducer";

test("accumulates events, totals, and judge cost", () => {
  let s = initialRunState();
  s = runReducer(s, {
    type: "event",
    data: { step: 1, agent: "a", tool: "read_file", output: "x", is_error: true, total_tokens: 12, total_cost: 0.001, agent_cost: 0.0008, judge_cost: 0.0002 },
  });
  s = runReducer(s, {
    type: "event",
    data: { step: 2, agent: "a", tool: "read_file", output: "y", is_error: true, total_tokens: 24, total_cost: 0.003, agent_cost: 0.0025, judge_cost: 0.0005 },
  });
  expect(s.events.length).toBe(2);
  expect(s.totalTokens).toBe(24);
  expect(s.totalCost).toBeCloseTo(0.003);
  expect(s.judgeCost).toBeCloseTo(0.0005); // judge cost surfaced separately
});

test("sets and clears the pending decision", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "decision_required", data: { detector: "semantic", judge_reasoning: "stuck", suggested_message: "fix" } });
  expect(s.pending?.judge_reasoning).toBe("stuck");
  expect(s.status).toBe("awaiting_decision");
  s = runReducer(s, { type: "status", data: { status: "running" } });
  expect(s.pending).toBeNull();
  expect(s.status).toBe("running");
});

test("logs auto-fixes", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "auto_fix", data: { step: 4, detector: "semantic", applied_fix: "read config.json", terminated: false } });
  expect(s.autoFixes.length).toBe(1);
  expect(s.autoFixes[0].applied_fix).toBe("read config.json");
});

test("tracks the allowlist", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "allowlisted", data: { tools: ["read_file"], allowlist: ["read_file"] } });
  expect(s.allowlist).toEqual(["read_file"]);
});

test("done sets final summary and status", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "done", data: { status: "completed", final_text: "done", summary: { events: 3 } } });
  expect(s.status).toBe("completed");
  expect(s.finalText).toBe("done");
});

test("error sets error state", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "error", data: { message: "boom" } });
  expect(s.status).toBe("error");
  expect(s.error).toBe("boom");
});
