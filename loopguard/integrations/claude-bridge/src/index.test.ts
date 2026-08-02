import assert from "node:assert/strict";
import test from "node:test";
import type {
  CanUseTool,
  ModelInfo,
  Options,
  PermissionResult,
  SDKMessage,
} from "@anthropic-ai/claude-agent-sdk";
import {
  BridgeProtocolError,
  BridgeRuntime,
  type BridgeCommand,
  type BridgeEvent,
  type QueryHandle,
} from "./index.js";

const MODELS: ModelInfo[] = [
  {
    value: "claude-test",
    resolvedModel: "claude-test-20260701",
    displayName: "Claude Test",
    description: "Test model",
    supportsEffort: true,
    supportedEffortLevels: ["low", "medium", "high"],
  },
];

class FakeQuery implements QueryHandle {
  readonly messages: SDKMessage[] = [];
  readonly waiters: Array<(value: IteratorResult<SDKMessage>) => void> = [];
  interrupted = false;
  closed = false;

  async interrupt() {
    this.interrupted = true;
    return undefined;
  }

  async initializationResult() {
    return {
      commands: [],
      agents: [],
      output_style: "default",
      available_output_styles: [],
      models: MODELS,
      account: { apiProvider: "firstParty" as const, apiKeySource: "ANTHROPIC_API_KEY" },
    };
  }

  close() {
    this.closed = true;
    for (const waiter of this.waiters.splice(0)) waiter({ done: true, value: undefined });
  }

  emit(message: SDKMessage) {
    const waiter = this.waiters.shift();
    if (waiter !== undefined) waiter({ done: false, value: message });
    else this.messages.push(message);
  }

  [Symbol.asyncIterator](): AsyncIterator<SDKMessage> {
    return {
      next: async () => {
        const message = this.messages.shift();
        if (message !== undefined) return { done: false, value: message };
        if (this.closed) return { done: true, value: undefined };
        return new Promise<IteratorResult<SDKMessage>>((resolve) => this.waiters.push(resolve));
      },
    };
  }
}

function startCommand(updates: Partial<BridgeCommand & { method: "start" }> = {}): BridgeCommand {
  return {
    id: "request-1",
    method: "start",
    params: {
      protocolVersion: 1,
      cwd: "/repo",
      model: "claude-test",
      effort: "medium",
      prompt: "Fix the regression.",
      permissionMode: "default",
      sandboxPolicy: "workspace-write",
      maxBudgetUsd: 2,
      maxTokens: 20_000,
      authProvider: "anthropic-api",
    },
    ...updates,
  } as BridgeCommand;
}

function setup() {
  const events: BridgeEvent[] = [];
  let query: FakeQuery | undefined;
  let options: Options | undefined;
  const runtime = new BridgeRuntime({
    emit: (event) => {
      events.push(event);
    },
    authProvider: () => "anthropic-api",
    queryFactory: (params) => {
      options = params.options;
      query = new FakeQuery();
      return query;
    },
  });
  return {
    runtime,
    events,
    query: () => {
      assert(query);
      return query;
    },
    options: () => {
      assert(options);
      return options;
    },
  };
}

test("start pins model, effort, auth, and normalizes tool and usage events", async () => {
  const fixture = setup();
  const result = await fixture.runtime.handle(startCommand());
  const sessionId = result.sessionId as string;
  assert.equal(result.authProvider, "anthropic-api");
  assert.deepEqual(result.models, [
    {
      value: "claude-test",
      resolvedModel: "claude-test-20260701",
      displayName: "Claude Test",
      supportsEffort: true,
      supportedEffortLevels: ["low", "medium", "high"],
    },
  ]);
  assert.equal(fixture.options().model, "claude-test");
  assert.equal(fixture.options().sessionId, sessionId);
  assert.equal(fixture.options().effort, "medium");
  assert.equal(fixture.options().env?.CLAUDE_CODE_OAUTH_TOKEN, undefined);
  assert.equal(fixture.options().sandbox?.enabled, true);
  assert.deepEqual(fixture.options().sandbox?.filesystem?.allowWrite, ["/repo"]);

  fixture.query().emit({
    type: "assistant",
    message: {
      id: "message-1",
      type: "message",
      role: "assistant",
      model: "claude-test",
      content: [{ type: "tool_use", id: "tool-1", name: "Bash", input: { command: "pytest" } }],
      stop_reason: null,
      stop_sequence: null,
      usage: { input_tokens: 1, output_tokens: 1 },
    },
    parent_tool_use_id: null,
    uuid: "message-uuid-1",
    session_id: sessionId,
  } as unknown as SDKMessage);
  fixture.query().emit({
    type: "result",
    subtype: "success",
    duration_ms: 10,
    duration_api_ms: 8,
    is_error: false,
    num_turns: 1,
    result: "done",
    stop_reason: "end_turn",
    total_cost_usd: 0.01,
    usage: { input_tokens: 10, output_tokens: 5 },
    modelUsage: {},
    permission_denials: [],
    uuid: "result-1",
    session_id: sessionId,
  } as unknown as SDKMessage);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(
    fixture.events.map((event) => event.params.event.kind),
    ["tool.call", "turn.completed"],
  );
  assert.equal(fixture.events[1]?.params.event.payload.costSource, "sdk_reported");
  await fixture.runtime.handle({
    id: "inject-1",
    method: "inject",
    params: { protocolVersion: 1, sessionId, text: "Next verified step." },
  });
});

test("permission resolution is separate from injection and interrupt uses Query.interrupt", async () => {
  const fixture = setup();
  const result = await fixture.runtime.handle(startCommand());
  const sessionId = result.sessionId as string;
  const canUseTool = fixture.options().canUseTool as CanUseTool;
  const abort = new AbortController();
  const pending = canUseTool(
    "Bash",
    { command: "git push" },
    {
      signal: abort.signal,
      toolUseID: "tool-1",
      requestId: "permission-1",
      decisionReason: "network access",
    },
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(fixture.events[0]?.params.event.kind, "action.requested");
  await assert.rejects(
    fixture.runtime.handle({
      id: "inject",
      method: "inject",
      params: { protocolVersion: 1, sessionId, text: "not approval" },
    }),
    (error: unknown) => error instanceof BridgeProtocolError && error.code === "stale_state",
  );
  await fixture.runtime.handle({
    id: "resolve",
    method: "resolve_permission",
    params: { sessionId, permissionId: "permission-1", behavior: "allow" },
  });
  assert.deepEqual(await pending, { behavior: "allow" } satisfies PermissionResult);
  await fixture.runtime.handle({
    id: "interrupt",
    method: "interrupt",
    params: { protocolVersion: 1, sessionId },
  });
  assert.equal(fixture.query().interrupted, true);
});

test("unsupported model, effort, auth, oversized input, and unknown session fail closed", async () => {
  const unsupported = setup();
  await assert.rejects(
    unsupported.runtime.handle(
      startCommand({ params: { ...startCommand().params, model: "unknown" } } as never),
    ),
    (error: unknown) => error instanceof BridgeProtocolError && error.code === "unsupported_model",
  );
  assert.equal(unsupported.query().closed, true);

  const noAuth = new BridgeRuntime({
    emit: () => undefined,
    authProvider: () => null,
    queryFactory: () => new FakeQuery(),
  });
  await assert.rejects(
    noAuth.handle(startCommand()),
    (error: unknown) =>
      error instanceof BridgeProtocolError && error.code === "authentication_unavailable",
  );

  const subscriptionQuery = new FakeQuery();
  subscriptionQuery.initializationResult = async () => ({
    commands: [],
    agents: [],
    output_style: "default",
    available_output_styles: [],
    models: MODELS,
    account: {
      apiProvider: "firstParty",
      apiKeySource: "subscription",
      subscriptionType: "max",
      tokenSource: "oauth",
    },
  });
  const subscription = new BridgeRuntime({
    emit: () => undefined,
    authProvider: () => "anthropic-api",
    queryFactory: () => subscriptionQuery,
  });
  await assert.rejects(
    subscription.handle(startCommand()),
    (error: unknown) =>
      error instanceof BridgeProtocolError && error.code === "subscription_auth_forbidden",
  );
  assert.equal(subscriptionQuery.closed, true);

  const fixture = setup();
  const result = await fixture.runtime.handle(startCommand());
  const sessionId = result.sessionId as string;
  await assert.rejects(
    fixture.runtime.handle({
      id: "oversized",
      method: "inject",
      params: { protocolVersion: 1, sessionId, text: "x".repeat(70_000) },
    }),
    (error: unknown) => error instanceof BridgeProtocolError && error.code === "oversized_text",
  );
  await assert.rejects(
    fixture.runtime.handle({
      id: "unknown",
      method: "interrupt",
      params: { protocolVersion: 1, sessionId: "missing" },
    }),
    (error: unknown) => error instanceof BridgeProtocolError && error.code === "unknown_session",
  );
  await assert.rejects(
    fixture.runtime.handle({ id: "bad", method: "bogus", params: {} } as never),
    (error: unknown) => error instanceof BridgeProtocolError && error.code === "unsupported_method",
  );
});
