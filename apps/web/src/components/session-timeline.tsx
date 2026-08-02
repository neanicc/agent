"use client";

import { useEffect, useMemo, useReducer, useRef, useState } from "react";

import { connectSessionStream, type SessionStreamEvent } from "@/lib/api/session-stream";


export type TimelineEvent = {
  type: "event";
  sessionSeq: number;
  clientStreamSeq: number;
  eventId: string;
  kind: string;
  occurredAt?: string;
  payload: Record<string, unknown>;
};

export type TimelineState = {
  items: TimelineEvent[];
  pending: TimelineEvent[];
  lastSessionSeq: number;
  lastClientStreamSeq: number;
  status: "idle" | "live" | "resyncing";
  replayAfterSessionSeq?: number;
};

export function initialTimeline(events: TimelineEvent[] = []): TimelineState {
  const unique = deduplicate(events).sort(compareEvents);
  return {
    items: unique,
    pending: [],
    lastSessionSeq: unique.at(-1)?.sessionSeq ?? 0,
    lastClientStreamSeq: Math.max(0, ...unique.map((event) => event.clientStreamSeq)),
    status: unique.length > 0 ? "live" : "idle",
  };
}

export function timelineAt(cursor: { session_seq: number; client_stream_seq: number }): TimelineState;
export function timelineAt(cursor: { sessionSeq: number; clientStreamSeq: number }): TimelineState;
export function timelineAt(
  cursor:
    | { session_seq: number; client_stream_seq: number }
    | { sessionSeq: number; clientStreamSeq: number },
): TimelineState {
  const sessionSeq = "sessionSeq" in cursor ? cursor.sessionSeq : cursor.session_seq;
  const clientStreamSeq =
    "clientStreamSeq" in cursor ? cursor.clientStreamSeq : cursor.client_stream_seq;
  return initialTimeline([
    {
      type: "event",
      sessionSeq,
      clientStreamSeq,
      eventId: `seed-${sessionSeq}`,
      kind: "cursor",
      payload: {},
    },
  ]);
}

export function reduceTimeline(state: TimelineState, event: TimelineEvent): TimelineState {
  const lastClientStreamSeq = Math.max(state.lastClientStreamSeq, event.clientStreamSeq);
  const known = [...state.items, ...state.pending].some((item) => item.eventId === event.eventId);
  if (known) {
    return {
      ...state,
      lastClientStreamSeq,
      lastSessionSeq: Math.max(state.lastSessionSeq, event.sessionSeq),
    };
  }

  if (state.lastSessionSeq > 0 && event.sessionSeq > state.lastSessionSeq + 1) {
    return {
      ...state,
      pending: [...state.pending, event].sort(compareEvents),
      status: "resyncing",
      replayAfterSessionSeq: state.replayAfterSessionSeq ?? state.lastSessionSeq,
      lastClientStreamSeq,
    };
  }

  let items = [...state.items, event].sort(compareEvents);
  let lastSessionSeq = Math.max(state.lastSessionSeq, event.sessionSeq);
  const pending = [...state.pending];
  let advanced = true;
  while (advanced) {
    advanced = false;
    const nextIndex = pending.findIndex((item) => item.sessionSeq <= lastSessionSeq + 1);
    if (nextIndex !== -1) {
      const [next] = pending.splice(nextIndex, 1);
      items = [...items, next].sort(compareEvents);
      lastSessionSeq = Math.max(lastSessionSeq, next.sessionSeq);
      advanced = true;
    }
  }
  const resyncing = pending.some((item) => item.sessionSeq > lastSessionSeq + 1);
  return {
    items,
    pending,
    lastSessionSeq,
    lastClientStreamSeq,
    status: resyncing ? "resyncing" : "live",
    replayAfterSessionSeq: resyncing ? state.replayAfterSessionSeq ?? lastSessionSeq : undefined,
  };
}

type SessionTimelineProps = {
  sessionId: string;
  initialEvents?: Array<Record<string, unknown>>;
};

export function SessionTimeline({ sessionId, initialEvents = [] }: SessionTimelineProps) {
  const seed = useMemo(
    () => initialEvents.map(normalizeRecord).filter((item): item is TimelineEvent => item !== null),
    [initialEvents],
  );
  const [state, dispatch] = useReducer(reduceTimeline, seed, initialTimeline);
  const [connection, setConnection] = useState<"connecting" | "live" | "reconnecting">(
    "connecting",
  );
  const appliedCursor = useRef(state.lastSessionSeq);
  const replayCursor = state.status === "resyncing" ? state.replayAfterSessionSeq : undefined;

  useEffect(() => {
    appliedCursor.current = state.lastSessionSeq;
  }, [state.lastSessionSeq]);

  useEffect(() => {
    const stream = connectSessionStream({
      sessionId,
      afterSessionSeq: replayCursor ?? appliedCursor.current,
      onEvent: (value) => dispatch(normalizeStreamEvent(sessionId, value)),
      onStatus: setConnection,
    });
    return () => stream.close();
  }, [sessionId, replayCursor]);

  return (
    <section aria-labelledby="timeline-title" className="timeline">
      <div className="section-heading">
        <div>
          <h2 id="timeline-title">Timeline</h2>
          <p>Phase boundaries and durable events. Tool payloads stay collapsed.</p>
        </div>
        <span className="status-label" data-status={state.status === "resyncing" ? "warning" : "neutral"}>
          {state.status === "resyncing" ? "Resyncing" : connectionLabel(connection)}
        </span>
      </div>
      {state.items.length === 0 ? (
        <p className="empty-copy">No timeline events have arrived yet.</p>
      ) : (
        <ol className="timeline-list">
          {state.items.map((item) => (
            <li className="timeline-event" key={item.eventId}>
              <div className="timeline-event__marker" aria-hidden="true" />
              <div className="timeline-event__content">
                <div className="timeline-event__heading">
                  <strong>{humanize(item.kind)}</strong>
                  <span className="telemetry">#{item.sessionSeq}</span>
                </div>
                {item.occurredAt ? <time dateTime={item.occurredAt}>{formatTime(item.occurredAt)}</time> : null}
                {Object.keys(item.payload).length > 0 ? (
                  <details>
                    <summary>Raw event evidence</summary>
                    <pre>{JSON.stringify(item.payload, null, 2)}</pre>
                  </details>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function normalizeStreamEvent(sessionId: string, event: SessionStreamEvent): TimelineEvent {
  const payloadId = typeof event.payload.event_id === "string" ? event.payload.event_id : undefined;
  return {
    type: "event",
    sessionSeq: event.session_seq,
    clientStreamSeq: event.client_stream_seq,
    eventId: event.event_id ?? payloadId ?? `${sessionId}:${event.session_seq}`,
    kind: event.kind ?? (typeof event.payload.kind === "string" ? event.payload.kind : "event"),
    occurredAt:
      event.occurred_at ??
      (typeof event.payload.occurred_at === "string" ? event.payload.occurred_at : undefined),
    payload: event.payload,
  };
}

function normalizeRecord(value: Record<string, unknown>): TimelineEvent | null {
  const sessionSeq = Number(value.session_seq);
  const clientStreamSeq = Number(value.client_stream_seq ?? sessionSeq);
  if (!Number.isSafeInteger(sessionSeq) || sessionSeq < 1 || !Number.isSafeInteger(clientStreamSeq)) {
    return null;
  }
  return {
    type: "event",
    sessionSeq,
    clientStreamSeq,
    eventId: String(value.event_id ?? `initial:${sessionSeq}`),
    kind: String(value.kind ?? "event"),
    occurredAt: typeof value.occurred_at === "string" ? value.occurred_at : undefined,
    payload: isRecord(value.payload) ? value.payload : {},
  };
}

function deduplicate(events: TimelineEvent[]): TimelineEvent[] {
  const seen = new Set<string>();
  return events.filter((event) => {
    if (seen.has(event.eventId)) return false;
    seen.add(event.eventId);
    return true;
  });
}

function compareEvents(left: TimelineEvent, right: TimelineEvent): number {
  return left.sessionSeq - right.sessionSeq || left.clientStreamSeq - right.clientStreamSeq;
}

function connectionLabel(status: "connecting" | "live" | "reconnecting") {
  if (status === "live") return "Live";
  if (status === "reconnecting") return "Reconnecting";
  return "Connecting";
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(date);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
