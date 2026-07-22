import { StatusLabel } from "./status-label";

export function VerificationProof({ proof }: { proof: Record<string, unknown> }) {
  const present = Object.keys(proof).length > 0;
  return (
    <section aria-labelledby="proof-heading" className="evidence-section">
      <div className="section-heading">
        <div>
          <h2 id="proof-heading">Verification proof</h2>
          <p>Deterministic result and provenance before raw output.</p>
        </div>
        <StatusLabel state={text(proof.verdict ?? proof.status, "inconclusive")} />
      </div>
      {present ? (
        <dl className="fact-grid">
          <ProofFact label="Verdict" value={text(proof.verdict ?? proof.status, "Inconclusive")} />
          <ProofFact label="Command" value={text(proof.command, "Not reported")} mono />
          <ProofFact label="Artifact" value={text(proof.artifact_id, "Not attached")} mono />
          <ProofFact label="Observed" value={formatDate(text(proof.observed_at ?? proof.completed_at))} />
        </dl>
      ) : (
        <p className="missing-evidence">No verification proof is attached to this item.</p>
      )}
    </section>
  );
}

function ProofFact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "telemetry" : undefined} title={value}>
        {value}
      </dd>
    </div>
  );
}

function text(value: unknown, fallback = ""): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function formatDate(value: string): string {
  if (!value) return "Not reported";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
