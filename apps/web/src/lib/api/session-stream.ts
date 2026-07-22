export type SessionStreamEvent = {
  type: "event";
  session_seq: number;
  client_stream_seq: number;
  event_id?: string;
  kind?: string;
  occurred_at?: string;
  payload: Record<string, unknown>;
};

type TicketResponse = {
  stream_url: string;
  expires_at: string;
};

type StreamOptions = {
  sessionId: string;
  afterSessionSeq: number;
  onEvent: (event: SessionStreamEvent) => void;
  onStatus?: (status: "connecting" | "live" | "reconnecting") => void;
  onError?: (error: Error) => void;
  fetcher?: typeof fetch;
  createSocket?: (url: string) => WebSocket;
};

export type SessionStreamConnection = { close: () => void };

export function connectSessionStream(options: StreamOptions): SessionStreamConnection {
  const fetcher = options.fetcher ?? fetch;
  const createSocket = options.createSocket ?? ((url: string) => new WebSocket(url));
  let socket: WebSocket | undefined;
  let stopped = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let attempt = 0;
  let cursor = options.afterSessionSeq;

  const connect = async () => {
    if (stopped) return;
    options.onStatus?.(attempt === 0 ? "connecting" : "reconnecting");
    try {
      const response = await fetcher("/api/stream-ticket", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": readCsrfToken(),
        },
        body: JSON.stringify({ session_id: options.sessionId, after_session_seq: cursor }),
      });
      if (!response.ok) {
        throw new Error(`Stream ticket request failed with HTTP ${response.status}`);
      }
      const ticket = (await response.json()) as TicketResponse;
      const url = new URL(ticket.stream_url);
      if (!new Set(["ws:", "wss:"]).has(url.protocol) || url.username || url.password) {
        throw new Error("Stream ticket returned an invalid WebSocket URL");
      }
      if (stopped) return;
      socket = createSocket(url.toString());
      socket.addEventListener("open", () => {
        attempt = 0;
        options.onStatus?.("live");
      });
      socket.addEventListener("message", (message) => {
        const parsed = parseMessage(message.data);
        if (parsed === null || parsed.type !== "event") return;
        cursor = Math.max(cursor, parsed.session_seq);
        options.onEvent(parsed);
      });
      socket.addEventListener("close", () => scheduleReconnect());
      socket.addEventListener("error", () => {
        options.onError?.(new Error("Session stream connection failed"));
        socket?.close();
      });
    } catch (error) {
      options.onError?.(error instanceof Error ? error : new Error("Session stream failed"));
      scheduleReconnect();
    }
  };

  const scheduleReconnect = () => {
    if (stopped || reconnectTimer !== undefined) return;
    attempt += 1;
    options.onStatus?.("reconnecting");
    const delay = Math.min(10_000, 250 * 2 ** Math.min(attempt, 5));
    reconnectTimer = setTimeout(() => {
      reconnectTimer = undefined;
      void connect();
    }, delay);
  };

  void connect();
  return {
    close() {
      stopped = true;
      if (reconnectTimer !== undefined) clearTimeout(reconnectTimer);
      socket?.close();
    },
  };
}

function parseMessage(value: unknown): SessionStreamEvent | null {
  try {
    const parsed = JSON.parse(String(value)) as Partial<SessionStreamEvent> & { type?: string };
    if (parsed.type !== "event") return null;
    if (
      !Number.isSafeInteger(parsed.session_seq) ||
      Number(parsed.session_seq) < 1 ||
      !Number.isSafeInteger(parsed.client_stream_seq) ||
      Number(parsed.client_stream_seq) < 1 ||
      typeof parsed.payload !== "object" ||
      parsed.payload === null
    ) {
      return null;
    }
    return parsed as SessionStreamEvent;
  } catch {
    return null;
  }
}

function readCsrfToken(): string {
  if (typeof document === "undefined") return "";
  return document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "";
}
