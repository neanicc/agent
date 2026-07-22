"use client";

import { useMemo, useState } from "react";

import { StatusLabel } from "./status-label";

export type ActionReviewRequest = {
  actionId: string;
  state: string;
  target: { kind: string; id: string; label: string };
  effect: string;
  risk: string;
  parametersHash: string;
  expectedState: string;
  expiresAt: string;
  approveLabel?: string;
  hostAvailable?: boolean;
};

export type ActionSnapshot = {
  state: string;
  resolvedAt?: string;
  receiptId?: string;
};

export type ActionTransport = {
  approve: (request: ActionReviewRequest) => Promise<ActionSnapshot>;
  reject: (request: ActionReviewRequest) => Promise<ActionSnapshot>;
  read: (actionId: string) => Promise<ActionSnapshot>;
};

type ActionSheetProps = {
  request: ActionReviewRequest;
  transport?: ActionTransport;
  now?: () => Date;
};

const submittedActionIds = new Set<string>();
const terminalStates = new Set(["executed", "rejected", "expired", "revoked", "stale"]);
const invalidReviewStates = new Set(["expired", "revoked", "stale", "executed", "rejected"]);

export function ActionSheet({ request, transport, now = () => new Date() }: ActionSheetProps) {
  const [stored, setStored] = useState<{
    actionId: string;
    snapshot: ActionSnapshot;
    busy: boolean;
    error?: string;
  }>({ actionId: request.actionId, snapshot: { state: request.state }, busy: false });
  const local =
    stored.actionId === request.actionId
      ? stored
      : { actionId: request.actionId, snapshot: { state: request.state }, busy: false };
  const { snapshot, busy, error } = local;
  const expiresAt = useMemo(() => new Date(request.expiresAt), [request.expiresAt]);
  const expired = !Number.isFinite(expiresAt.valueOf()) || expiresAt.valueOf() <= now().valueOf();
  const currentState = expired && !terminalStates.has(snapshot.state) ? "expired" : snapshot.state;
  const terminal = terminalStates.has(currentState);
  const canSubmit =
    Boolean(transport) &&
    !busy &&
    !expired &&
    request.hostAvailable !== false &&
    !invalidReviewStates.has(currentState) &&
    !submittedActionIds.has(request.actionId);

  const resolve = async (decision: "approve" | "reject") => {
    if (!transport || !canSubmit) return;
    submittedActionIds.add(request.actionId);
    setStored({ actionId: request.actionId, snapshot: { state: "signed" }, busy: true });
    try {
      const result = await transport[decision](request);
      updateStored(request.actionId, { snapshot: result, busy: true });
    } catch {
      updateStored(request.actionId, { snapshot: { state: "reconciling" }, busy: true });
      try {
        const authoritative = await transport.read(request.actionId);
        updateStored(request.actionId, { snapshot: authoritative, busy: true });
      } catch {
        updateStored(request.actionId, {
          error: "The action outcome is unknown. Refresh the current action state before trying anything else.",
          busy: true,
        });
      }
    } finally {
      updateStored(request.actionId, { busy: false });
    }
  };

  const updateStored = (
    actionId: string,
    update: Partial<{ snapshot: ActionSnapshot; busy: boolean; error?: string }>,
  ) => {
    setStored((current) => (current.actionId === actionId ? { ...current, ...update } : current));
  };

  return (
    <section aria-labelledby={`action-${request.actionId}`} className="action-sheet">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Explicit confirmation</p>
          <h2 id={`action-${request.actionId}`}>Review safe action</h2>
          <p>Confirm the immutable target and expected result. Acceptance is not execution.</p>
        </div>
        <StatusLabel label={stateLabel(currentState)} state={currentState} />
      </div>

      <div className="action-sheet__effect">
        <span>Effect</span>
        <strong>{request.effect}</strong>
      </div>

      <dl className="action-review-grid">
        <ActionFact label="Target" value={request.target.label} />
        <ActionFact label="Risk" value={request.risk} />
        <ActionFact label="Expected state" value={request.expectedState} />
        <ActionFact label="Parameters hash" value={request.parametersHash} mono />
        <ActionFact label="Action ID" value={request.actionId} mono />
        <ActionFact
          label="Expiry"
          value={expired ? `Expired ${formatDate(request.expiresAt)}` : `Expires ${formatDate(request.expiresAt)}`}
        />
      </dl>

      <ActionLifecycle state={currentState} />

      {terminal && (currentState === "executed" || currentState === "rejected") ? (
        <section aria-label="Execution receipt" className="action-receipt">
          <h3>Execution receipt</h3>
          <dl>
            <ActionFact label="Outcome" value={stateLabel(currentState)} />
            <ActionFact label="Receipt" value={snapshot.receiptId ?? request.actionId} mono />
            <ActionFact label="Resolved" value={formatDate(snapshot.resolvedAt ?? "")} />
          </dl>
        </section>
      ) : null}

      {error ? (
        <p aria-live="polite" className="action-sheet__error">
          {error}
        </p>
      ) : null}

      <div className="action-sheet__controls">
        <button
          className="button"
          disabled={!canSubmit}
          onClick={() => void resolve("approve")}
          type="button"
        >
          {approveButtonLabel({ request, currentState, expired, busy, hasTransport: Boolean(transport) })}
        </button>
        <button
          className="button button--secondary"
          disabled={!canSubmit}
          onClick={() => void resolve("reject")}
          type="button"
        >
          Reject request
        </button>
      </div>
      <span aria-live="polite" className="visually-hidden">
        {busy ? "Submitting one action" : stateLabel(currentState)}
      </span>
    </section>
  );
}

function ActionLifecycle({ state }: { state: string }) {
  const steps = ["reviewed", "signed", "queued", "delivered", "host_executing", "executed"];
  const currentIndex = steps.indexOf(state);
  return (
    <ol aria-label="Action delivery state" className="action-lifecycle">
      {steps.map((step, index) => (
        <li data-progress={progressFor(step, state, index, currentIndex)} key={step}>
          <span aria-hidden="true" />
          {stateLabel(step)}
        </li>
      ))}
      {["rejected", "expired", "revoked", "stale", "reconciling"].includes(state) ? (
        <li data-progress="current">
          <span aria-hidden="true" />
          {stateLabel(state)}
        </li>
      ) : null}
    </ol>
  );
}

function progressFor(step: string, state: string, index: number, currentIndex: number) {
  if (step === state) return "current";
  if (currentIndex >= 0 && index < currentIndex) return "complete";
  return "pending";
}

function ActionFact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "telemetry" : undefined} title={value}>
        {value}
      </dd>
    </div>
  );
}

function approveButtonLabel({
  request,
  currentState,
  expired,
  busy,
  hasTransport,
}: {
  request: ActionReviewRequest;
  currentState: string;
  expired: boolean;
  busy: boolean;
  hasTransport: boolean;
}) {
  if (expired || currentState === "expired") return "Approval expired";
  if (currentState === "revoked") return "Approval revoked";
  if (currentState === "stale") return "Refresh required";
  if (request.hostAvailable === false) return "Host offline";
  if (!hasTransport) return "Approval setup required";
  if (busy || ["signed", "reconciling"].includes(currentState)) return stateLabel(currentState);
  if (submittedActionIds.has(request.actionId)) return stateLabel(currentState);
  return request.approveLabel ?? "Approve action";
}

function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    reviewed: "Reviewed",
    signed: "Signed",
    queued: "Queued for host",
    delivered: "Delivered to host",
    host_executing: "Host executing",
    executing: "Host executing",
    executed: "Executed",
    rejected: "Rejected",
    expired: "Expired",
    revoked: "Revoked",
    stale: "Stale — refresh required",
    reconciling: "Reconciling outcome",
  };
  return labels[state] ?? state.replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase());
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (!value || Number.isNaN(date.valueOf())) return "Not reported";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(date);
}
