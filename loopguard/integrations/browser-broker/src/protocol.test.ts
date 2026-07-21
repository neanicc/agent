import assert from "node:assert/strict";
import test from "node:test";
import {
  FrameDecoder,
  MAX_FRAME_BYTES,
  PROTOCOL_VERSION,
  encodeFrame,
  parseCommand,
  parseHandshake,
  parseResponse,
} from "./protocol.js";

test("accepts every strict broker command", () => {
  const contextCapability = "c".repeat(64);
  assert.equal(
    parseCommand({
      id: "1",
      method: "context.create",
      params: {
        sessionId: "s1",
        browser: "chromium",
        storageStatePath: null,
        allowedOrigins: ["https://example.test"],
      },
    }).method,
    "context.create",
  );
  assert.equal(
    parseCommand({
      id: "2",
      method: "context.close",
      params: { contextId: contextCapability },
    }).method,
    "context.close",
  );
  assert.equal(
    parseCommand({
      id: "3",
      method: "page.run",
      params: {
        contextId: contextCapability,
        timeoutMs: 1_000,
        actions: [{ type: "goto", url: "https://example.test" }],
      },
    }).method,
    "page.run",
  );
  assert.equal(parseCommand({ id: "4", method: "health", params: {} }).method, "health");
});

test("rejects unknown fields, browser names, and unbounded action values", () => {
  assert.throws(() =>
    parseCommand({
      id: "1",
      method: "context.create",
      params: {
        sessionId: "s1",
        browser: "netscape",
        storageStatePath: null,
        secret: "x",
      },
    }),
  );
  assert.throws(() =>
    parseCommand({
      id: "2",
      method: "page.run",
      params: {
        contextId: "ctx",
        timeoutMs: 1_000,
        actions: [{ type: "fill", selector: "#password", value: "x".repeat(70_000) }],
      },
    }),
  );
});

test("requires the exact protocol version and a high-entropy connection capability", () => {
  const capability = "b".repeat(64);
  assert.deepEqual(
    parseHandshake({ type: "hello", protocolVersion: PROTOCOL_VERSION, capability }),
    { type: "hello", protocolVersion: 1, capability },
  );
  assert.throws(() =>
    parseHandshake({ type: "hello", protocolVersion: 2, capability }),
  );
  assert.throws(() =>
    parseHandshake({ type: "hello", protocolVersion: 1, capability: "guessable" }),
  );
});

test("validates success and secret-safe error response envelopes", () => {
  assert.deepEqual(parseResponse({ id: "1", ok: true, result: { activeContexts: 0 } }), {
    id: "1",
    ok: true,
    result: { activeContexts: 0 },
  });
  assert.throws(() =>
    parseResponse({
      id: "1",
      ok: false,
      error: { code: "bad_request", message: "invalid", storageState: "secret" },
    }),
  );
});

test("decodes split and coalesced bounded length-prefixed frames", () => {
  const first = encodeFrame({ id: "1", method: "health", params: {} });
  const second = encodeFrame({ id: "2", method: "health", params: {} });
  const decoder = new FrameDecoder();
  assert.deepEqual(decoder.push(first.subarray(0, 3)), []);
  assert.deepEqual(decoder.push(Buffer.concat([first.subarray(3), second])), [
    { id: "1", method: "health", params: {} },
    { id: "2", method: "health", params: {} },
  ]);
});

test("rejects oversized, empty, malformed, and unknown-field frames", () => {
  assert.throws(() => encodeFrame({ body: "x".repeat(MAX_FRAME_BYTES) }));
  const empty = Buffer.alloc(4);
  assert.throws(() => new FrameDecoder().push(empty));
  const oversized = Buffer.alloc(4);
  oversized.writeUInt32BE(MAX_FRAME_BYTES + 1);
  assert.throws(() => new FrameDecoder().push(oversized));
  const malformed = Buffer.alloc(5);
  malformed.writeUInt32BE(1);
  malformed[4] = 0xff;
  assert.throws(() => new FrameDecoder().push(malformed));
  const invalidUtf8 = Buffer.from('{"value":"x"}');
  invalidUtf8[10] = 0xff;
  const invalidUtf8Frame = Buffer.alloc(4 + invalidUtf8.byteLength);
  invalidUtf8Frame.writeUInt32BE(invalidUtf8.byteLength);
  invalidUtf8.copy(invalidUtf8Frame, 4);
  assert.throws(() => new FrameDecoder().push(invalidUtf8Frame));
});
