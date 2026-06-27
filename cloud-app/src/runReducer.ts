export type ServerMessage = { type: string; data?: any };

export type RunEvent = {
  step?: number;
  agent?: string;
  tool?: string;
  args?: Record<string, any>;
  output?: string;
  is_error?: boolean;
  tripped?: boolean;
};

export type PendingDecision = {
  detector?: string;
  similarity?: number;
  reason?: string;
  judge_reasoning?: string;
  judge_confidence?: number;
  suggested_message?: string | null;
};

export type AutoFix = {
  step?: number;
  detector?: string;
  judge_reasoning?: string;
  applied_fix?: string | null;
  terminated?: boolean;
};

export type RunUiState = {
  status: string;
  events: RunEvent[];
  totalTokens: number;
  totalCost: number;
  agentCost: number;
  judgeCost: number;
  pending: PendingDecision | null;
  autoFixes: AutoFix[];
  allowlist: string[];
  finalText: string | null;
  error: string | null;
  summary: Record<string, any>;
};

export function initialRunState(): RunUiState {
  return {
    status: "connecting",
    events: [],
    totalTokens: 0,
    totalCost: 0,
    agentCost: 0,
    judgeCost: 0,
    pending: null,
    autoFixes: [],
    allowlist: [],
    finalText: null,
    error: null,
    summary: {},
  };
}

export function runReducer(state: RunUiState, msg: ServerMessage): RunUiState {
  const d = msg.data || {};
  switch (msg.type) {
    case "event":
      return {
        ...state,
        status: state.status === "connecting" ? "running" : state.status,
        events: [...state.events, d as RunEvent],
        totalTokens: d.total_tokens ?? state.totalTokens,
        totalCost: d.total_cost ?? state.totalCost,
        agentCost: d.agent_cost ?? state.agentCost,
        judgeCost: d.judge_cost ?? state.judgeCost,
      };
    case "decision_required":
      return { ...state, status: "awaiting_decision", pending: d as PendingDecision };
    case "auto_fix":
      return { ...state, autoFixes: [...state.autoFixes, d as AutoFix] };
    case "allowlisted":
      return { ...state, allowlist: d.allowlist ?? state.allowlist };
    case "status":
      return { ...state, status: d.status ?? state.status, pending: null };
    case "done":
      return {
        ...state,
        status: d.status ?? "completed",
        pending: null,
        finalText: d.final_text ?? state.finalText,
        summary: d.summary ?? state.summary,
      };
    case "error":
      return { ...state, status: "error", error: d.message ?? "unknown error" };
    default:
      return state;
  }
}
