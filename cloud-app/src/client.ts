export type StartOpts = { mode: "flag" | "auto"; model: string };

export class LoopGuardClient {
  constructor(public baseUrl: string) {}

  private http() {
    return this.baseUrl.replace(/\/+$/, "");
  }
  private wsBase() {
    return this.http().replace(/^http/, "ws");
  }

  async health(): Promise<boolean> {
    try {
      const r = await fetch(`${this.http()}/health`, { method: "GET" });
      return r.ok;
    } catch {
      return false;
    }
  }

  async startRun(opts: StartOpts): Promise<string> {
    const r = await fetch(`${this.http()}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opts),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({} as any));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    return (await r.json()).run_id as string;
  }

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
