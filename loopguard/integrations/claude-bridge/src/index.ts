import { randomUUID } from "node:crypto";
import { isAbsolute, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import {
  query,
  type AccountInfo,
  type CanUseTool,
  type EffortLevel,
  type ModelInfo,
  type Options,
  type PermissionMode,
  type PermissionResult,
  type Query,
  type SDKMessage,
  type SDKUserMessage,
} from "@anthropic-ai/claude-agent-sdk";

const PROTOCOL_VERSION = 1;
const MAX_FRAME_BYTES = 1_048_576;
const MAX_TEXT_BYTES = 64 * 1024;
const MAX_SESSIONS = 64;
const EFFORTS = new Set<EffortLevel>(["low", "medium", "high", "xhigh", "max"]);
const PERMISSION_MODES = new Set<PermissionMode>([
  "default",
  "acceptEdits",
  "plan",
  "dontAsk",
  "auto",
]);
const AUTH_PROVIDERS = new Set<AuthProvider>([
  "anthropic-api",
  "bedrock",
  "vertex",
  "foundry",
]);

type AuthProvider = "anthropic-api" | "bedrock" | "vertex" | "foundry";
type SessionStatus =
  | "starting"
  | "running"
  | "idle"
  | "requires_action"
  | "interrupting"
  | "terminal";

type StartParams = {
  protocolVersion: 1;
  cwd: string;
  model: string;
  effort: EffortLevel;
  prompt: string;
  permissionMode: PermissionMode;
  sandboxPolicy: "read-only" | "workspace-write" | "danger-full-access";
  maxBudgetUsd: number;
  maxTokens: number;
  authProvider: AuthProvider;
};

export type BridgeCommand =
  | { id: string | number; method: "start"; params: StartParams }
  | {
      id: string | number;
      method: "interrupt" | "close";
      params: { protocolVersion: 1; sessionId: string };
    }
  | {
      id: string | number;
      method: "inject";
      params: { protocolVersion: 1; sessionId: string; text: string };
    }
  | {
      id: string | number;
      method: "resolve_permission";
      params: {
        protocolVersion?: 1;
        sessionId: string;
        permissionId: string;
        behavior: "allow" | "deny";
      };
    };

export type BridgeEvent = {
  method: "event";
  params: {
    protocolVersion: 1;
    sessionId: string;
    event: {
      kind: string;
      eventId: string;
      turnId?: string;
      payload: Record<string, unknown>;
    };
  };
};

type QueryOptions = { prompt: AsyncIterable<SDKUserMessage>; options: Options };
export type QueryFactory = (params: QueryOptions) => QueryHandle;
export type QueryHandle = Pick<Query, "interrupt" | "initializationResult" | "close"> &
  AsyncIterable<SDKMessage>;
type Emit = (event: BridgeEvent) => void | Promise<void>;

type PendingPermission = {
  resolve: (value: PermissionResult) => void;
  input: Record<string, unknown>;
};

type ManagedSession = {
  id: string;
  status: SessionStatus;
  query: QueryHandle;
  input: InputChannel;
  pendingPermissions: Map<string, PendingPermission>;
  maxTokens: number;
  observedTokens: number;
};

export class BridgeRuntime {
  readonly sessions = new Map<string, ManagedSession>();
  private readonly queryFactory: QueryFactory;
  private readonly emit: Emit;
  private readonly authProvider: () => AuthProvider | null;

  constructor(options: {
    queryFactory?: QueryFactory;
    emit: Emit;
    authProvider?: () => AuthProvider | null;
  }) {
    this.queryFactory = options.queryFactory ?? ((params) => query(params));
    this.emit = options.emit;
    this.authProvider = options.authProvider ?? (() => detectAuthentication(process.env));
  }

  async handle(command: BridgeCommand): Promise<Record<string, unknown>> {
    validateCommand(command);
    switch (command.method) {
      case "start":
        return this.start(command.params);
      case "interrupt":
        return this.interrupt(command.params.sessionId);
      case "inject":
        return this.inject(command.params.sessionId, command.params.text);
      case "resolve_permission":
        return this.resolvePermission(
          command.params.sessionId,
          command.params.permissionId,
          command.params.behavior,
        );
      case "close":
        return this.close(command.params.sessionId);
      default:
        throw new BridgeProtocolError("unsupported_method", "bridge method is unsupported");
    }
  }

  private async start(params: StartParams): Promise<Record<string, unknown>> {
    if (this.sessions.size >= MAX_SESSIONS) {
      throw new BridgeProtocolError("session_limit", "managed session limit reached");
    }
    validateStart(params);
    const detected = this.authProvider();
    if (detected === null || detected !== params.authProvider) {
      throw new BridgeProtocolError(
        "authentication_unavailable",
        "supported API/provider authentication is unavailable",
      );
    }
    const sessionId = randomUUID();
    const input = new InputChannel();
    let session: ManagedSession;
    const canUseTool: CanUseTool = async (toolName, toolInput, options) =>
      this.requestPermission(session, toolName, toolInput, options);
    const handle = this.queryFactory({
      prompt: input,
      options: {
        cwd: params.cwd,
        sessionId,
        model: params.model,
        effort: params.effort,
        permissionMode: params.permissionMode,
        maxBudgetUsd: params.maxBudgetUsd,
        canUseTool,
        env: safeEnvironment(process.env, detected),
        sandbox: sandboxSettings(params.sandboxPolicy, params.cwd),
      },
    });
    session = {
      id: sessionId,
      status: "starting",
      query: handle,
      input,
      pendingPermissions: new Map(),
      maxTokens: params.maxTokens,
      observedTokens: 0,
    };
    this.sessions.set(sessionId, session);
    try {
      const initialization = await handle.initializationResult();
      validateSdkAuthentication(initialization.account, detected);
      const models = initialization.models;
      validateModelSelection(models, params.model, params.effort);
      session.status = "running";
      void this.consume(session);
      input.push(params.prompt);
      return {
        protocolVersion: PROTOCOL_VERSION,
        sessionId,
        models: normalizeModels(models),
        authProvider: detected,
      };
    } catch (error) {
      session.status = "terminal";
      input.close();
      handle.close();
      this.sessions.delete(sessionId);
      throw error;
    }
  }

  private async interrupt(sessionId: string): Promise<Record<string, unknown>> {
    const session = this.owned(sessionId);
    if (!["running", "requires_action"].includes(session.status)) {
      throw new BridgeProtocolError("stale_state", "session has no active turn");
    }
    for (const pending of session.pendingPermissions.values()) {
      pending.resolve({ behavior: "deny", message: "LoopGuard interrupted the turn", interrupt: true });
    }
    session.pendingPermissions.clear();
    session.status = "interrupting";
    await session.query.interrupt();
    return { protocolVersion: PROTOCOL_VERSION, accepted: true };
  }

  private async inject(sessionId: string, text: string): Promise<Record<string, unknown>> {
    const session = this.owned(sessionId);
    validateText(text, "injected text");
    if (session.status !== "idle" || session.pendingPermissions.size > 0) {
      throw new BridgeProtocolError(
        "stale_state",
        "between-turn input cannot resolve an in-flight turn or permission",
      );
    }
    session.status = "running";
    session.input.push(text);
    return { protocolVersion: PROTOCOL_VERSION, accepted: true };
  }

  private async resolvePermission(
    sessionId: string,
    permissionId: string,
    behavior: "allow" | "deny",
  ): Promise<Record<string, unknown>> {
    const session = this.owned(sessionId);
    if (session.status !== "requires_action") {
      throw new BridgeProtocolError("stale_state", "session is not waiting for permission");
    }
    const pending = session.pendingPermissions.get(permissionId);
    if (pending === undefined) {
      throw new BridgeProtocolError("unknown_permission", "permission id is not pending");
    }
    session.pendingPermissions.delete(permissionId);
    pending.resolve(
      behavior === "allow"
        ? { behavior: "allow" }
        : { behavior: "deny", message: "LoopGuard denied this tool request" },
    );
    if (session.pendingPermissions.size === 0) {
      session.status = "running";
    }
    return { protocolVersion: PROTOCOL_VERSION, accepted: true };
  }

  private async close(sessionId: string): Promise<Record<string, unknown>> {
    const session = this.owned(sessionId);
    session.status = "terminal";
    session.input.close();
    for (const pending of session.pendingPermissions.values()) {
      pending.resolve({ behavior: "deny", message: "Managed session closed", interrupt: true });
    }
    session.pendingPermissions.clear();
    session.query.close();
    return { protocolVersion: PROTOCOL_VERSION, closed: true };
  }

  private async requestPermission(
    session: ManagedSession,
    toolName: string,
    input: Record<string, unknown>,
    options: Parameters<CanUseTool>[2],
  ): Promise<PermissionResult> {
    if (session.status === "terminal") {
      return { behavior: "deny", message: "Managed session is closed" };
    }
    const permissionId = boundedIdentifier(options.requestId, "permission id");
    if (session.pendingPermissions.has(permissionId)) {
      throw new BridgeProtocolError("duplicate_permission", "permission id is already pending");
    }
    session.status = "requires_action";
    const result = new Promise<PermissionResult>((resolvePermission) => {
      session.pendingPermissions.set(permissionId, { resolve: resolvePermission, input });
    });
    const abort = () => {
      const pending = session.pendingPermissions.get(permissionId);
      if (pending !== undefined) {
        session.pendingPermissions.delete(permissionId);
        pending.resolve({ behavior: "deny", message: "Permission request was aborted" });
      }
    };
    options.signal.addEventListener("abort", abort, { once: true });
    await this.emitEvent(session, {
      kind: "action.requested",
      eventId: `permission:${permissionId}`,
      payload: {
        permissionId,
        toolName,
        input,
        reason: options.decisionReason ?? options.title ?? null,
        toolUseId: options.toolUseID,
      },
    });
    try {
      return await result;
    } finally {
      options.signal.removeEventListener("abort", abort);
    }
  }

  private async consume(session: ManagedSession): Promise<void> {
    try {
      for await (const message of session.query) {
        if (session.status === "terminal") {
          throw new BridgeProtocolError("event_after_terminal", "SDK emitted after terminal state");
        }
        await this.normalizeMessage(session, message);
      }
      if (session.status !== "terminal") {
        session.status = "terminal";
        await this.emitEvent(session, {
          kind: "session.stopped",
          eventId: `session-stopped:${session.id}`,
          payload: { reason: "sdk_stream_closed" },
        });
      }
    } catch (error) {
      if (session.status !== "terminal") {
        session.status = "terminal";
        await this.emitEvent(session, {
          kind: "pipeline.failed",
          eventId: `pipeline-failed:${session.id}`,
          payload: { error: safeError(error) },
        });
      }
    } finally {
      session.input.close();
      this.sessions.delete(session.id);
    }
  }

  private async normalizeMessage(session: ManagedSession, message: SDKMessage): Promise<void> {
    const sessionId = "session_id" in message ? message.session_id : undefined;
    if (sessionId !== undefined && sessionId !== session.id) {
      throw new BridgeProtocolError("session_mismatch", "SDK message session id does not match");
    }
    if (message.type === "assistant") {
      for (const block of message.message.content) {
        if (block.type === "tool_use") {
          await this.emitEvent(session, {
            kind: "tool.call",
            eventId: `${message.uuid}:tool:${block.id}`,
            payload: { toolName: block.name, toolUseId: block.id, input: block.input },
          });
        }
      }
      return;
    }
    if (message.type === "user" && Array.isArray(message.message.content)) {
      for (const block of message.message.content) {
        if (block.type === "tool_result") {
          await this.emitEvent(session, {
            kind: "tool.result",
            eventId: `${message.uuid ?? randomUUID()}:result:${block.tool_use_id}`,
            payload: {
              toolUseId: block.tool_use_id,
              isError: block.is_error ?? false,
              content: block.content,
            },
          });
        }
      }
      return;
    }
    if (message.type === "result") {
      const usage = message.usage as unknown as Record<string, unknown>;
      session.observedTokens += numericTokenTotal(usage);
      session.status = "idle";
      await this.emitEvent(session, {
        kind: "turn.completed",
        eventId: message.uuid,
        payload: {
          subtype: message.subtype,
          usage,
          modelUsage: message.modelUsage,
          reportedCostUsd: message.total_cost_usd,
          costSource: "sdk_reported",
          terminalReason: message.terminal_reason ?? null,
          tokenBudgetExceeded: session.observedTokens > session.maxTokens,
        },
      });
      if (session.observedTokens > session.maxTokens) {
        await session.query.interrupt();
      }
      return;
    }
    if (
      message.type === "system" &&
      message.subtype === "session_state_changed" &&
      message.state === "running"
    ) {
      session.status = "running";
    }
  }

  private async emitEvent(
    session: ManagedSession,
    event: BridgeEvent["params"]["event"],
  ): Promise<void> {
    await this.emit({
      method: "event",
      params: { protocolVersion: PROTOCOL_VERSION, sessionId: session.id, event },
    });
  }

  private owned(sessionId: string): ManagedSession {
    const normalized = boundedIdentifier(sessionId, "session id");
    const session = this.sessions.get(normalized);
    if (session === undefined) {
      throw new BridgeProtocolError("unknown_session", "managed session is unknown");
    }
    if (session.status === "terminal") {
      throw new BridgeProtocolError("terminal_session", "managed session is terminal");
    }
    return session;
  }
}

class InputChannel implements AsyncIterable<SDKUserMessage> {
  private readonly queued: SDKUserMessage[] = [];
  private readonly waiters: Array<(value: IteratorResult<SDKUserMessage>) => void> = [];
  private closed = false;

  push(text: string): void {
    validateText(text, "prompt");
    if (this.closed) {
      throw new BridgeProtocolError("closed_input", "session input is closed");
    }
    const message: SDKUserMessage = {
      type: "user",
      message: { role: "user", content: text },
      parent_tool_use_id: null,
      origin: { kind: "human" },
    };
    const waiter = this.waiters.shift();
    if (waiter !== undefined) {
      waiter({ done: false, value: message });
    } else {
      this.queued.push(message);
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    for (const waiter of this.waiters.splice(0)) {
      waiter({ done: true, value: undefined });
    }
  }

  [Symbol.asyncIterator](): AsyncIterator<SDKUserMessage> {
    return {
      next: async () => {
        const value = this.queued.shift();
        if (value !== undefined) return { done: false, value };
        if (this.closed) return { done: true, value: undefined };
        return new Promise<IteratorResult<SDKUserMessage>>((resolveNext) => {
          this.waiters.push(resolveNext);
        });
      },
    };
  }
}

export class BridgeProtocolError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "BridgeProtocolError";
  }
}

function validateCommand(command: BridgeCommand): void {
  const raw = command as unknown as Record<string, unknown>;
  if (command === null || typeof command !== "object" || Array.isArray(command)) {
    throw new BridgeProtocolError("invalid_command", "bridge command must be an object");
  }
  if (
    !(
      (typeof raw.id === "string" && raw.id.length > 0 && raw.id.length <= 256) ||
      (typeof raw.id === "number" && Number.isSafeInteger(raw.id))
    )
  ) {
    throw new BridgeProtocolError("invalid_id", "bridge command id is invalid");
  }
  if (
    typeof raw.method !== "string" ||
    !["start", "interrupt", "inject", "resolve_permission", "close"].includes(raw.method)
  ) {
    throw new BridgeProtocolError("unsupported_method", "bridge method is unsupported");
  }
  if (raw.params === null || typeof raw.params !== "object" || Array.isArray(raw.params)) {
    throw new BridgeProtocolError("invalid_params", "bridge params must be an object");
  }
  const params = raw.params as Record<string, unknown>;
  if (params.protocolVersion !== undefined && params.protocolVersion !== 1) {
    throw new BridgeProtocolError("protocol_version", "bridge protocol version is unsupported");
  }
}

function validateStart(params: StartParams): void {
  if (params.protocolVersion !== PROTOCOL_VERSION) {
    throw new BridgeProtocolError("protocol_version", "bridge protocol version is unsupported");
  }
  if (!isAbsolute(params.cwd)) {
    throw new BridgeProtocolError("invalid_cwd", "managed cwd must be absolute");
  }
  validateText(params.model, "model", 256);
  validateText(params.prompt, "prompt", MAX_TEXT_BYTES);
  if (!EFFORTS.has(params.effort)) {
    throw new BridgeProtocolError("unsupported_effort", "effort level is unsupported");
  }
  if (!PERMISSION_MODES.has(params.permissionMode) || params.permissionMode === "bypassPermissions") {
    throw new BridgeProtocolError("permission_mode", "permission mode is unsupported");
  }
  if (!AUTH_PROVIDERS.has(params.authProvider)) {
    throw new BridgeProtocolError("authentication", "authentication provider is unsupported");
  }
  if (
    !(["read-only", "workspace-write", "danger-full-access"] as string[]).includes(
      params.sandboxPolicy,
    )
  ) {
    throw new BridgeProtocolError("sandbox_policy", "sandbox policy is unsupported");
  }
  if (!Number.isFinite(params.maxBudgetUsd) || params.maxBudgetUsd < 0) {
    throw new BridgeProtocolError("budget", "USD budget is invalid");
  }
  if (!Number.isSafeInteger(params.maxTokens) || params.maxTokens <= 0) {
    throw new BridgeProtocolError("budget", "token budget is invalid");
  }
}

function validateModelSelection(models: ModelInfo[], model: string, effort: EffortLevel): void {
  const selected = models.find(
    (candidate) => candidate.value === model || candidate.resolvedModel === model,
  );
  if (selected === undefined) {
    throw new BridgeProtocolError("unsupported_model", "requested model is not advertised");
  }
  if (selected.supportsEffort !== true || !selected.supportedEffortLevels?.includes(effort)) {
    throw new BridgeProtocolError("unsupported_effort", "requested effort is not advertised");
  }
}

function validateSdkAuthentication(account: AccountInfo, provider: AuthProvider): void {
  const tokenSource = `${account.tokenSource ?? ""} ${account.apiKeySource ?? ""}`.toLowerCase();
  if (
    account.subscriptionType !== undefined ||
    tokenSource.includes("oauth") ||
    tokenSource.includes("subscription") ||
    tokenSource.includes("claude.ai")
  ) {
    throw new BridgeProtocolError(
      "subscription_auth_forbidden",
      "claude.ai login and subscription credentials are not supported",
    );
  }
  const expectedProvider = {
    "anthropic-api": "firstParty",
    bedrock: "bedrock",
    vertex: "vertex",
    foundry: "foundry",
  } as const;
  if (account.apiProvider !== undefined && account.apiProvider !== expectedProvider[provider]) {
    throw new BridgeProtocolError(
      "authentication_mismatch",
      "SDK initialized a different authentication provider",
    );
  }
  if (provider === "anthropic-api" && !account.apiKeySource) {
    throw new BridgeProtocolError(
      "api_key_unproven",
      "SDK did not confirm API-key authentication",
    );
  }
}

function normalizeModels(models: ModelInfo[]): Array<Record<string, unknown>> {
  return models.slice(0, 100).map((model) => ({
    value: model.value,
    resolvedModel: model.resolvedModel,
    displayName: model.displayName,
    supportsEffort: model.supportsEffort ?? false,
    supportedEffortLevels: model.supportedEffortLevels ?? [],
  }));
}

function detectAuthentication(environment: NodeJS.ProcessEnv): AuthProvider | null {
  if (environment.CLAUDE_CODE_OAUTH_TOKEN) return null;
  if (environment.ANTHROPIC_API_KEY) return "anthropic-api";
  if (truthy(environment.CLAUDE_CODE_USE_BEDROCK)) return "bedrock";
  if (truthy(environment.CLAUDE_CODE_USE_VERTEX)) return "vertex";
  if (truthy(environment.CLAUDE_CODE_USE_FOUNDRY)) return "foundry";
  return null;
}

function safeEnvironment(
  environment: NodeJS.ProcessEnv,
  provider: AuthProvider,
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(environment)) {
    if (value !== undefined && key !== "CLAUDE_CODE_OAUTH_TOKEN") result[key] = value;
  }
  if (provider === "anthropic-api") {
    delete result.CLAUDE_CODE_USE_BEDROCK;
    delete result.CLAUDE_CODE_USE_VERTEX;
    delete result.CLAUDE_CODE_USE_FOUNDRY;
  }
  return result;
}

function sandboxSettings(
  policy: StartParams["sandboxPolicy"],
  cwd: string,
): NonNullable<Options["sandbox"]> {
  if (policy === "danger-full-access") return { enabled: false };
  return {
    enabled: true,
    failIfUnavailable: true,
    autoAllowBashIfSandboxed: false,
    allowUnsandboxedCommands: false,
    filesystem: {
      allowWrite: policy === "workspace-write" ? [resolve(cwd)] : [],
      allowRead: [resolve(cwd)],
    },
    network: {
      allowedDomains: [],
      allowManagedDomainsOnly: true,
      allowAllUnixSockets: false,
      allowLocalBinding: false,
    },
  };
}

function numericTokenTotal(usage: Record<string, unknown>): number {
  return Object.entries(usage).reduce(
    (total, [key, value]) =>
      key.endsWith("_tokens") && typeof value === "number" && Number.isFinite(value)
        ? total + Math.max(0, value)
        : total,
    0,
  );
}

function boundedIdentifier(value: string, label: string): string {
  if (typeof value !== "string" || value.length === 0 || value.length > 256) {
    throw new BridgeProtocolError("invalid_identifier", `${label} is invalid`);
  }
  return value;
}

function validateText(value: string, label: string, maxBytes = MAX_TEXT_BYTES): void {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new BridgeProtocolError("invalid_text", `${label} must be non-empty`);
  }
  if (Buffer.byteLength(value, "utf8") > maxBytes) {
    throw new BridgeProtocolError("oversized_text", `${label} exceeds its byte limit`);
  }
}

function truthy(value: string | undefined): boolean {
  return value !== undefined && ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

function safeError(error: unknown): string {
  if (error instanceof BridgeProtocolError) return `${error.code}: ${error.message}`;
  return "managed SDK bridge failed";
}

async function runStdio(): Promise<void> {
  let output = Promise.resolve();
  const write = (value: Record<string, unknown> | BridgeEvent): Promise<void> => {
    output = output.then(
      () =>
        new Promise<void>((resolveWrite, rejectWrite) => {
          const encoded = `${JSON.stringify(value)}\n`;
          if (Buffer.byteLength(encoded) > MAX_FRAME_BYTES) {
            rejectWrite(new BridgeProtocolError("oversized_frame", "outbound frame is too large"));
            return;
          }
          if (process.stdout.write(encoded)) resolveWrite();
          else process.stdout.once("drain", resolveWrite);
        }),
    );
    return output;
  };
  const runtime = new BridgeRuntime({ emit: (event) => write(event) });
  let buffered = Buffer.alloc(0);
  for await (const chunk of process.stdin) {
    buffered = Buffer.concat([buffered, Buffer.from(chunk)]);
    if (buffered.length > MAX_FRAME_BYTES && !buffered.includes(0x0a)) {
      throw new BridgeProtocolError("oversized_frame", "inbound frame is too large");
    }
    let newline = buffered.indexOf(0x0a);
    while (newline >= 0) {
      const line = buffered.subarray(0, newline);
      buffered = buffered.subarray(newline + 1);
      if (line.length > 0) await processLine(runtime, line, write);
      newline = buffered.indexOf(0x0a);
    }
  }
  for (const session of runtime.sessions.values()) session.query.close();
}

async function processLine(
  runtime: BridgeRuntime,
  line: Buffer,
  write: (value: Record<string, unknown> | BridgeEvent) => Promise<void>,
): Promise<void> {
  let raw: unknown;
  try {
    raw = JSON.parse(line.toString("utf8"));
  } catch {
    throw new BridgeProtocolError("malformed_json", "inbound frame is not valid JSON");
  }
  if (raw === null || typeof raw !== "object" || !("id" in raw)) {
    throw new BridgeProtocolError("invalid_command", "command requires a correlation id");
  }
  const id = (raw as { id: unknown }).id;
  try {
    const result = await runtime.handle(raw as BridgeCommand);
    await write({ id, result });
  } catch (error) {
    const failure =
      error instanceof BridgeProtocolError
        ? error
        : new BridgeProtocolError("internal", "bridge request failed");
    await write({
      id,
      error: {
        code: jsonRpcErrorCode(failure.code),
        message: failure.message,
        data: { bridgeCode: failure.code },
      },
    });
  }
}

function jsonRpcErrorCode(code: string): number {
  if (code === "unsupported_method") return -32601;
  if (code.startsWith("invalid_") || code === "protocol_version") return -32602;
  if (code === "internal") return -32603;
  return -32000;
}

const invoked = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (invoked === import.meta.url) {
  runStdio().catch(() => {
    process.stderr.write("LoopGuard Claude bridge terminated safely\n");
    process.exitCode = 1;
  });
}
