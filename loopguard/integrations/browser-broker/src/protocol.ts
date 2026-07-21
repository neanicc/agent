import { z } from "zod";

export const PROTOCOL_VERSION = 1 as const;
export const MAX_FRAME_BYTES = 1_048_576;
export const MAX_PENDING_REQUESTS = 128;
const MAX_ACTIONS = 256;
const MAX_TEXT_BYTES = 64 * 1024;

const identifier = z
  .string()
  .trim()
  .min(1)
  .max(256)
  .refine((value) => !value.includes("\0"), "identifier contains a null byte");
const requestId = z
  .string()
  .trim()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9._:-]+$/);
const contextId = z
  .string()
  .min(32)
  .max(256)
  .regex(/^[A-Za-z0-9_-]+$/);
const absoluteUrl = z
  .string()
  .max(2_048)
  .url()
  .refine((value) => new URL(value).protocol === "http:" || new URL(value).protocol === "https:");
const boundedText = z
  .string()
  .max(MAX_TEXT_BYTES)
  .refine((value) => !value.includes("\0"), "text contains a null byte");

const gotoAction = z.strictObject({
  type: z.literal("goto"),
  url: absoluteUrl,
  waitUntil: z.enum(["commit", "domcontentloaded", "load", "networkidle"]).optional(),
});
const clickAction = z.strictObject({
  type: z.literal("click"),
  selector: boundedText.min(1),
});
const fillAction = z.strictObject({
  type: z.literal("fill"),
  selector: boundedText.min(1),
  value: boundedText,
});
const screenshotAction = z.strictObject({
  type: z.literal("screenshot"),
  name: z.string().min(1).max(128).regex(/^[A-Za-z0-9][A-Za-z0-9._-]*$/),
  fullPage: z.boolean().optional(),
});
const artifactName = z.string().min(1).max(128).regex(/^[A-Za-z0-9][A-Za-z0-9._-]*$/);
const uploadAction = z.strictObject({
  type: z.literal("upload"),
  selector: boundedText.min(1),
  artifactName,
});
const downloadAction = z.strictObject({
  type: z.literal("download"),
  selector: boundedText.min(1),
  name: artifactName,
});
const pageAction = z.discriminatedUnion("type", [
  gotoAction,
  clickAction,
  fillAction,
  screenshotAction,
  uploadAction,
  downloadAction,
]);

const createContextParams = z.strictObject({
  sessionId: identifier,
  browser: z.enum(["chromium", "firefox", "webkit"]),
  storageStatePath: z.string().min(1).max(4_096).nullable(),
  allowedOrigins: z.array(absoluteUrl).max(128).default([]),
  locale: z.string().min(2).max(64).optional(),
  timezoneId: z.string().min(1).max(128).optional(),
  serviceWorkers: z.enum(["allow", "block"]).default("block"),
});
const closeContextParams = z.strictObject({ contextId, sessionId: identifier });
const runPageParams = z.strictObject({
  contextId,
  sessionId: identifier,
  actions: z.array(pageAction).max(MAX_ACTIONS),
  timeoutMs: z.number().int().min(1).max(120_000),
});
const connectBrowserParams = z.strictObject({
  sessionId: identifier,
  browser: z.enum(["chromium", "firefox", "webkit"]),
});
const releaseBrowserParams = z.strictObject({
  sessionId: identifier,
  leaseId: contextId,
});

const commandSchema = z.discriminatedUnion("method", [
  z.strictObject({ id: requestId, method: z.literal("context.create"), params: createContextParams }),
  z.strictObject({ id: requestId, method: z.literal("context.close"), params: closeContextParams }),
  z.strictObject({ id: requestId, method: z.literal("page.run"), params: runPageParams }),
  z.strictObject({ id: requestId, method: z.literal("browser.connect"), params: connectBrowserParams }),
  z.strictObject({ id: requestId, method: z.literal("browser.release"), params: releaseBrowserParams }),
  z.strictObject({ id: requestId, method: z.literal("health"), params: z.strictObject({}) }),
]);

const handshakeSchema = z.strictObject({
  type: z.literal("hello"),
  protocolVersion: z.literal(PROTOCOL_VERSION),
  capability: z.string().min(43).max(128).regex(/^[A-Za-z0-9_-]+$/),
});
const errorSchema = z.strictObject({
  code: z.string().min(1).max(64).regex(/^[a-z][a-z0-9_]*$/),
  message: z.string().min(1).max(1_024).refine((value) => !value.includes("\0")),
});
const responseSchema = z.discriminatedUnion("ok", [
  z.strictObject({ id: requestId, ok: z.literal(true), result: z.unknown() }),
  z.strictObject({ id: requestId, ok: z.literal(false), error: errorSchema }),
]);

export type BrokerCommand = z.infer<typeof commandSchema>;
export type CreateContextParams = z.infer<typeof createContextParams>;
export type RunPageParams = z.infer<typeof runPageParams>;
export type PageAction = z.infer<typeof pageAction>;
export type BrokerHandshake = z.infer<typeof handshakeSchema>;
export type BrokerResponse = z.infer<typeof responseSchema>;

export function parseCommand(value: unknown): BrokerCommand {
  return commandSchema.parse(value);
}

export function parseHandshake(value: unknown): BrokerHandshake {
  return handshakeSchema.parse(value);
}

export function parseResponse(value: unknown): BrokerResponse {
  if (
    typeof value === "object" &&
    value !== null &&
    "ok" in value &&
    value.ok === true &&
    !Object.hasOwn(value, "result")
  ) {
    throw new Error("successful broker response requires a result");
  }
  return responseSchema.parse(value);
}

export function encodeFrame(value: unknown): Buffer {
  let payload: Buffer;
  try {
    payload = Buffer.from(JSON.stringify(value), "utf8");
  } catch (error) {
    throw new Error("broker frame is not serializable JSON", { cause: error });
  }
  if (payload.byteLength === 0 || payload.byteLength > MAX_FRAME_BYTES) {
    throw new Error("broker frame exceeds the size limit");
  }
  const frame = Buffer.allocUnsafe(4 + payload.byteLength);
  frame.writeUInt32BE(payload.byteLength, 0);
  payload.copy(frame, 4);
  return frame;
}

export class FrameDecoder {
  #buffer = Buffer.alloc(0);

  push(chunk: Uint8Array): unknown[] {
    if (chunk.byteLength === 0) return [];
    this.#buffer = Buffer.concat([this.#buffer, Buffer.from(chunk)]);
    const values: unknown[] = [];
    while (this.#buffer.byteLength >= 4) {
      const length = this.#buffer.readUInt32BE(0);
      if (length === 0 || length > MAX_FRAME_BYTES) {
        this.#buffer = Buffer.alloc(0);
        throw new Error("broker frame length is invalid");
      }
      if (this.#buffer.byteLength < length + 4) {
        if (this.#buffer.byteLength > MAX_FRAME_BYTES + 4) {
          this.#buffer = Buffer.alloc(0);
          throw new Error("broker frame buffer exceeds the size limit");
        }
        break;
      }
      if (values.length >= MAX_PENDING_REQUESTS) {
        this.#buffer = Buffer.alloc(0);
        throw new Error("too many broker frames arrived at once");
      }
      const payload = this.#buffer.subarray(4, length + 4);
      this.#buffer = this.#buffer.subarray(length + 4);
      try {
        const text = new TextDecoder("utf-8", { fatal: true }).decode(payload);
        values.push(JSON.parse(text) as unknown);
      } catch (error) {
        this.#buffer = Buffer.alloc(0);
        throw new Error("broker frame contains malformed JSON", { cause: error });
      }
    }
    return values;
  }
}
