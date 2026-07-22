"use client";

import { ControlProblem } from "@/lib/api/errors";


type AsyncStateProps = {
  status: "loading" | "empty" | "error" | "stale" | "resyncing";
  title?: string;
  detail?: string;
  error?: unknown;
  onRetry?: () => void;
  command?: string;
};

export function AsyncState({ status, title, detail, error, onRetry, command }: AsyncStateProps) {
  if (status === "loading") {
    return (
      <div aria-busy="true" aria-label={title ?? "Loading"} className="async-state async-state--loading">
        <span className="skeleton skeleton--heading" />
        <span className="skeleton" />
        <span className="skeleton" />
        <span className="skeleton skeleton--short" />
      </div>
    );
  }

  const problem = error instanceof ControlProblem ? error : undefined;
  const defaults = {
    empty: ["Nothing here yet", "This destination will update when LoopGuard observes matching activity."],
    error: ["This view could not be loaded", problem?.message ?? "The control API did not return a usable response."],
    stale: ["Showing stale data", "The last valid result remains visible while LoopGuard reconnects."],
    resyncing: ["Resyncing events", "LoopGuard is replaying the durable gap before showing newer events."],
  } as const;
  const [defaultTitle, defaultDetail] = defaults[status];

  return (
    <section aria-live={status === "error" ? "polite" : undefined} className="async-state" data-state={status}>
      <h2>{title ?? defaultTitle}</h2>
      <p>{detail ?? defaultDetail}</p>
      {command ? <code className="async-state__command">{command}</code> : null}
      {problem?.requestId ? <p className="telemetry">Request {problem.requestId}</p> : null}
      {onRetry ? (
        <button className="button button--secondary" onClick={onRetry} type="button">
          Retry request
        </button>
      ) : null}
    </section>
  );
}
