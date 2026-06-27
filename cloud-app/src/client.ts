export type StartOpts = {
  projectId: string;
  mode: "flag" | "auto";
  model?: string;
  task?: string | null;
};

export type Project = {
  id: string;
  label: string;
  blurb: string;
  kind: "single" | "multi";
  task: string;
  agents: string[];
  customizable: boolean;
  hint?: string | null;
};

export type RunSummary = {
  id: string;
  project_id: string;
  label: string;
  kind: string;
  mode: string;
  model: string;
  status: string;
  events: number;
  agents: string[];
  summary: Record<string, any>;
  auto_fixes: number;
  allowlist: string[];
};

export type AllowlistEntry = {
  ts: number;
  tools: string[];
  detector?: string;
  reason?: string;
  run_id: string;
  project_id: string;
  label: string;
};

export type AutoFixEntry = {
  step: number;
  detector?: string;
  judge_reasoning?: string;
  judge_confidence?: number;
  applied_fix?: string | null;
  terminated?: boolean;
  run_id: string;
  project_id: string;
  label: string;
};

const DEFAULT_MODEL = "cerebras/gpt-oss-120b";

export class LoopGuardClient {
  constructor(public baseUrl: string) {}

  private http() {
    return this.baseUrl.replace(/\/+$/, "");
  }
  private wsBase() {
    return this.http().replace(/^http/, "ws");
  }

  private async getJSON<T>(path: string): Promise<T> {
    const r = await fetch(`${this.http()}${path}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return (await r.json()) as T;
  }

  async health(): Promise<boolean> {
    try {
      const r = await fetch(`${this.http()}/health`, { method: "GET" });
      return r.ok;
    } catch {
      return false;
    }
  }

  projects(): Promise<Project[]> {
    return this.getJSON<Project[]>("/projects");
  }
  runs(): Promise<RunSummary[]> {
    return this.getJSON<RunSummary[]>("/runs");
  }
  run(id: string): Promise<any> {
    return this.getJSON<any>(`/runs/${id}`);
  }
  allowlist(): Promise<AllowlistEntry[]> {
    return this.getJSON<AllowlistEntry[]>("/allowlist");
  }
  autofixes(): Promise<AutoFixEntry[]> {
    return this.getJSON<AutoFixEntry[]>("/autofixes");
  }

  async startRun(opts: StartOpts): Promise<string> {
    const r = await fetch(`${this.http()}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: opts.projectId,
        mode: opts.mode,
        model: opts.model || DEFAULT_MODEL,
        task: opts.task ?? null,
      }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}) as any);
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    return (await r.json()).run_id as string;
  }

  // Connect (or reconnect) to a run's live stream. The server replays current events
  // and any pending decision on connect, so this doubles as resync.
  openSocket(runId: string, onMessage: (m: any) => void, onError: (e: string) => void): WebSocket {
    const ws = new WebSocket(`${this.wsBase()}/runs/${runId}/ws`);
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data as string));
      } catch {
        /* ignore malformed frame */
      }
    };
    ws.onerror = () => onError("WebSocket error");
    return ws;
  }

  static intervene(ws: WebSocket, action: string, message?: string) {
    ws.send(JSON.stringify({ type: "intervene", action, message }));
  }
}
