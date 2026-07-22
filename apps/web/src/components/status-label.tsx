type StatusLabelProps = { state: string; label?: string };

export function StatusLabel({ state, label }: StatusLabelProps) {
  const tone = statusTone(state);
  return (
    <span className="status-label" data-status={tone}>
      <StatusIcon tone={tone} />
      {label ?? humanize(state)}
    </span>
  );
}

function StatusIcon({ tone }: { tone: string }) {
  if (tone === "danger") {
    return (
      <svg aria-hidden="true" viewBox="0 0 16 16">
        <path d="M5 5l6 6m0-6-6 6" />
      </svg>
    );
  }
  if (tone === "warning") {
    return (
      <svg aria-hidden="true" viewBox="0 0 16 16">
        <path d="M8 2.5l6 11H2l6-11zm0 3.2v3.6m0 2.1v.1" />
      </svg>
    );
  }
  if (tone === "success") {
    return (
      <svg aria-hidden="true" viewBox="0 0 16 16">
        <path d="M3 8.3l3.1 3.1L13 4.8" />
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16">
      <circle cx="8" cy="8" r="4.5" />
    </svg>
  );
}

function statusTone(state: string): "danger" | "warning" | "success" | "neutral" {
  const normalized = state.toLowerCase();
  if (/blocked|failed|rejected|revoked|offline|expired|regression|loop/.test(normalized)) return "danger";
  if (/warn|stale|partial|pending|inconclusive|resync|unknown/.test(normalized)) return "warning";
  if (/ready|passed|executed|current|healthy|complete/.test(normalized)) return "success";
  return "neutral";
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
}
