import { initialRunState, runReducer } from "../runReducer";

test("accumulates events and totals", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "event", data: { tool: "read_file", output: "x", is_error: true, total_tokens: 12, total_cost: 0.001 } });
  s = runReducer(s, { type: "event", data: { tool: "read_file", output: "y", is_error: true, total_tokens: 24, total_cost: 0.002 } });
  expect(s.events.length).toBe(2);
  expect(s.totalTokens).toBe(24);
  expect(s.totalCost).toBeCloseTo(0.002);
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

test("done sets final summary and status", () => {
  let s = initialRunState();
  s = runReducer(s, { type: "done", data: { status: "completed", final_text: "done", summary: { events: 3 } } });
  expect(s.status).toBe("completed");
  expect(s.finalText).toBe("done");
});
