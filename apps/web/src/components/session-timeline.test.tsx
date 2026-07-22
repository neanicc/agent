import { describe, expect, test } from "vitest";

import {
  initialTimeline,
  reduceTimeline,
  timelineAt,
  type TimelineEvent,
} from "./session-timeline";


const event = (overrides: Partial<TimelineEvent>): TimelineEvent => ({
  type: "event",
  sessionSeq: 1,
  clientStreamSeq: 1,
  eventId: "event-1",
  kind: "phase_changed",
  occurredAt: "2026-07-22T12:00:00Z",
  payload: {},
  ...overrides,
});

describe("session timeline replay", () => {
  test("replay and live duplicate event render once", () => {
    let state = initialTimeline();
    state = reduceTimeline(
      state,
      event({ sessionSeq: 4, clientStreamSeq: 9, eventId: "e4" }),
    );
    state = reduceTimeline(
      state,
      event({ sessionSeq: 4, clientStreamSeq: 10, eventId: "e4" }),
    );

    expect(state.items).toHaveLength(1);
    expect(state.lastSessionSeq).toBe(4);
    expect(state.lastClientStreamSeq).toBe(10);
  });

  test("cursor gap requests replay before applying live event", () => {
    const state = reduceTimeline(
      timelineAt({ sessionSeq: 4, clientStreamSeq: 10 }),
      event({ sessionSeq: 6, clientStreamSeq: 11, eventId: "e6" }),
    );

    expect(state.status).toBe("resyncing");
    expect(state.replayAfterSessionSeq).toBe(4);
    expect(state.items.map((item) => item.eventId)).not.toContain("e6");
    expect(state.pending.map((item) => item.eventId)).toEqual(["e6"]);
  });

  test("replay closes the gap before queued live events become visible", () => {
    let state = reduceTimeline(
      timelineAt({ sessionSeq: 4, clientStreamSeq: 10 }),
      event({ sessionSeq: 6, clientStreamSeq: 11, eventId: "e6" }),
    );
    state = reduceTimeline(
      state,
      event({ sessionSeq: 5, clientStreamSeq: 12, eventId: "e5" }),
    );

    expect(state.status).toBe("live");
    expect(state.items.map((item) => item.eventId)).toEqual(["seed-4", "e5", "e6"]);
    expect(state.pending).toEqual([]);
    expect(state.lastSessionSeq).toBe(6);
    expect(state.lastClientStreamSeq).toBe(12);
  });

  test("out-of-order replay is sorted without moving the durable cursor backwards", () => {
    let state = timelineAt({ sessionSeq: 4, clientStreamSeq: 10 });
    state = reduceTimeline(
      state,
      event({ sessionSeq: 3, clientStreamSeq: 11, eventId: "e3" }),
    );

    expect(state.items.map((item) => item.sessionSeq)).toEqual([3, 4]);
    expect(state.lastSessionSeq).toBe(4);
    expect(state.lastClientStreamSeq).toBe(11);
  });
});
