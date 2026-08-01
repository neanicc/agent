import crypto from "k6/crypto";
import http from "k6/http";
import { check } from "k6";

const baseUrl = __ENV.LOOPGUARD_BASE_URL || "http://127.0.0.1:8000";
const keyId = __ENV.LOOPGUARD_HOOK_KEY_ID;
const secret = __ENV.LOOPGUARD_HOOK_SECRET;
const repository = __ENV.LOOPGUARD_REPOSITORY_HANDLE;
const path = "/v1/hook-events";

export const options = {
  scenarios: {
    signed_ingest: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.LOOPGUARD_INGEST_RPS || 100),
      timeUnit: "1s",
      duration: __ENV.LOOPGUARD_LOAD_DURATION || "2m",
      preAllocatedVUs: Number(__ENV.LOOPGUARD_LOAD_VUS || 25),
      maxVUs: Number(__ENV.LOOPGUARD_LOAD_MAX_VUS || 200),
    },
  },
  thresholds: {
    checks: ["rate>0.999"],
    http_req_failed: ["rate<0.001"],
    http_req_duration: ["p(95)<250", "p(99)<750"],
  },
};

function required(name, value) {
  if (!value) {
    throw new Error(`${name} must be set; never put hook credentials in this script`);
  }
  return value;
}

function timestamp() {
  return new Date().toISOString().replace("Z", "+00:00");
}

function signature(body, observedAt, nonce) {
  const digest = crypto.sha256(body, "hex");
  const canonical = ["POST", path, observedAt, nonce, repository, digest].join("\n");
  const derived = crypto.sha256(`loopguard.hook.hmac.v1:${secret}`, "binary");
  return crypto.hmac("sha256", derived, canonical, "hex");
}

export function setup() {
  required("LOOPGUARD_HOOK_KEY_ID", keyId);
  required("LOOPGUARD_HOOK_SECRET", secret);
  required("LOOPGUARD_REPOSITORY_HANDLE", repository);
}

export default function () {
  const body = JSON.stringify({
    event_id: `load-${__VU}-${__ITER}-${Date.now()}`,
    kind: "load.probe",
    payload: { synthetic: true },
  });
  const observedAt = timestamp();
  const nonce = `${__VU}-${__ITER}-${Date.now()}`;
  const response = http.post(`${baseUrl}${path}`, body, {
    headers: {
      "Content-Type": "application/json",
      "X-LoopGuard-Key-ID": keyId,
      "X-LoopGuard-Timestamp": observedAt,
      "X-LoopGuard-Nonce": nonce,
      "X-LoopGuard-Repository": repository,
      "X-LoopGuard-Signature": signature(body, observedAt, nonce),
    },
    tags: { endpoint: "hook_ingest" },
  });
  check(response, {
    "ingest accepted": (value) => value.status === 202,
    "request id returned": (value) => Boolean(value.headers["X-Request-Id"]),
  });
}
