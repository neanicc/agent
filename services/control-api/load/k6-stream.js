import ws from "k6/ws";
import { check } from "k6";

const streamBaseUrl = __ENV.LOOPGUARD_WS_BASE_URL || "ws://127.0.0.1:8000";
const sessionId = __ENV.LOOPGUARD_SESSION_ID;
const accessToken = __ENV.LOOPGUARD_ACCESS_TOKEN;

export const options = {
  scenarios: {
    session_streams: {
      executor: "constant-vus",
      vus: Number(__ENV.LOOPGUARD_STREAM_CONNECTIONS || 20),
      duration: __ENV.LOOPGUARD_LOAD_DURATION || "2m",
    },
  },
  thresholds: {
    checks: ["rate>0.999"],
    ws_connecting: ["p(95)<250", "p(99)<750"],
    ws_session_duration: ["p(95)>1000"],
  },
};

export function setup() {
  if (!sessionId || !accessToken) {
    throw new Error(
      "LOOPGUARD_SESSION_ID and LOOPGUARD_ACCESS_TOKEN are required; use environment secrets",
    );
  }
}

export default function () {
  const response = ws.connect(
    `${streamBaseUrl}/v1/sessions/${sessionId}/stream?after_session_seq=0`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
    (socket) => {
      socket.on("open", () => socket.setTimeout(() => socket.close(), 5000));
      socket.on("message", (message) => {
        check(message, { "stream frame is JSON": (value) => Boolean(JSON.parse(value).type) });
      });
      socket.on("error", (error) => {
        if (!String(error.error()).includes("websocket: close sent")) {
          console.error("stream error");
        }
      });
    },
  );
  check(response, { "stream upgraded": (value) => value && value.status === 101 });
}
