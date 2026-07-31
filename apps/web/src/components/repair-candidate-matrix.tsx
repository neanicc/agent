import { StatusLabel } from "./status-label";


export type RepairCapabilities = {
  status?: string;
  observed_at?: string | null;
  ttl_seconds?: number;
  features?: Record<
    string,
    { available?: boolean; status?: string; reason?: string }
  >;
};

type Candidate = {
  id?: unknown;
  candidate_id?: unknown;
  strategy?: unknown;
  changed_files?: unknown;
  changed_lines?: unknown;
  diff?: unknown;
  evaluation?: unknown;
};

type Ranking = {
  winning_candidate_id?: unknown;
  winner_id?: unknown;
  reason?: unknown;
  rationale?: unknown;
};

export function isFreshRepairCapability(
  capability: RepairCapabilities | undefined,
  now = new Date(),
): boolean {
  const repair = capability?.features?.repair;
  const observedAt = new Date(capability?.observed_at ?? "").valueOf();
  const ttl = capability?.ttl_seconds;
  return (
    capability?.status === "ready" &&
    repair?.available === true &&
    (repair.status === undefined || repair.status === "ready") &&
    Number.isFinite(observedAt) &&
    typeof ttl === "number" &&
    Number.isFinite(ttl) &&
    ttl > 0 &&
    observedAt + ttl * 1_000 > now.valueOf()
  );
}

export function RepairCandidateMatrix({
  candidates,
  ranking,
}: {
  candidates: Candidate[];
  ranking: Ranking;
}) {
  const winner = text(
    ranking.winning_candidate_id ?? ranking.winner_id,
  );
  const reason = text(
    ranking.reason ?? ranking.rationale,
    "No deterministic ranking reason was reported.",
  );
  return (
    <section
      aria-labelledby="candidate-matrix-heading"
      className="evidence-section repair-candidates"
    >
      <div className="section-heading">
        <div>
          <h2 id="candidate-matrix-heading">Candidate evidence</h2>
          <p>
            Required checks and contract impact determine rank before patch
            size or speed.
          </p>
        </div>
        <span className="queue-count telemetry">{candidates.length}</span>
      </div>
      {candidates.length ? (
        <div className="candidate-matrix-wrap">
          <table className="candidate-matrix">
            <thead>
              <tr>
                <th scope="col">Candidate</th>
                <th scope="col">Replay</th>
                <th scope="col">Regression</th>
                <th scope="col">Security</th>
                <th scope="col">Contract</th>
                <th scope="col">Patch</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((candidate, index) => {
                const id = text(
                  candidate.id ?? candidate.candidate_id,
                  `candidate-${index + 1}`,
                );
                const evaluation = record(candidate.evaluation);
                const files = strings(candidate.changed_files);
                const isWinner = id === winner;
                const contractBreaking =
                  evaluation.contract_breaking === true ||
                  record(evaluation.contract_delta).breaking === true;
                return (
                  <tr data-winner={isWinner || undefined} key={id}>
                    <th scope="row">
                      <span className="candidate-title">
                        <code>{id}</code>
                        {isWinner ? (
                          <span className="candidate-winner">Winner</span>
                        ) : null}
                      </span>
                      <small>
                        {text(candidate.strategy, "Strategy not reported")}
                      </small>
                    </th>
                    <CheckCell
                      label="Replay"
                      value={evaluation.replay ?? evaluation.replay_passed}
                    />
                    <CheckCell
                      label="Regression"
                      value={
                        evaluation.regression ??
                        evaluation.regression_passed
                      }
                    />
                    <CheckCell
                      label="Security"
                      value={
                        evaluation.security ?? evaluation.security_passed
                      }
                    />
                    <td data-label="Contract">
                      <StatusLabel
                        label={contractBreaking ? "Breaking" : "Compatible"}
                        state={contractBreaking ? "failed" : "passed"}
                      />
                      <small>
                        {contractChangeCount(evaluation)} contract changes
                      </small>
                    </td>
                    <td data-label="Patch">
                      <span className="telemetry">
                        {files.length} files ·{" "}
                        {number(candidate.changed_lines)} lines
                      </span>
                      {files.length ? (
                        <small title={files.join(", ")}>
                          {files.join(", ")}
                        </small>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="missing-evidence">
          No candidate cleared the bounded generation stage.
        </p>
      )}
      <div className="ranking-reason">
        <span>Deterministic ranking</span>
        <p>{reason}</p>
      </div>
      {candidates.map((candidate, index) => {
        const diff = text(candidate.diff);
        const id = text(
          candidate.id ?? candidate.candidate_id,
          `candidate-${index + 1}`,
        );
        return diff ? (
          <details className="candidate-diff" key={`diff-${id}`}>
            <summary>Bounded diff · {id}</summary>
            <pre>{diff}</pre>
          </details>
        ) : null;
      })}
    </section>
  );
}

function CheckCell({ label, value }: { label: string; value: unknown }) {
  const passed = value === true || value === "passed";
  const state =
    value === undefined || value === null
      ? "inconclusive"
      : passed
        ? "passed"
        : "failed";
  return (
    <td data-label={label}>
      <StatusLabel state={state} />
    </td>
  );
}

function contractChangeCount(evaluation: Record<string, unknown>): number {
  const direct = evaluation.contract_changes;
  const nested = record(evaluation.contract_delta).changes;
  return Array.isArray(direct)
    ? direct.length
    : Array.isArray(nested)
      ? nested.length
      : 0;
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function number(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}
