"use client";

import { useMemo, useState } from "react";

import { controlClient, useControlQuery } from "@/lib/api/use-control-query";
import type { components } from "@/lib/api/generated";
import { AsyncState } from "./async-state";
import { PageFrame, PageHeader } from "./control-pages";
import { PolicyEditor, type PolicyProfile } from "./policy-editor";
import { StatusLabel } from "./status-label";

type CostSummary = components["schemas"]["CostSummaryView"];
type Device = components["schemas"]["DeviceView"];
type AuditEntry = components["schemas"]["AuditEntryView"];

export function PoliciesPage() {
  const query = useControlQuery<PolicyProfile>("/v1/preferences");
  return (
    <PageFrame>
      <PageHeader
        description="Inspect rule source and precedence before changing the effective safety profile."
        title="Policies"
      />
      {query.isPending ? <AsyncState status="loading" title="Loading effective policy" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {query.data ? <PolicyEditor profile={query.data} /> : null}
    </PageFrame>
  );
}

export function CostsPage() {
  const [window, setWindow] = useState<CostSummary["window"]>("30d");
  const query = useControlQuery<CostSummary>(`/v1/costs?window=${window}`);
  const categories = query.data
    ? (Object.entries(query.data.observed) as Array<[keyof CostSummary["observed"], string]>)
    : [];
  return (
    <PageFrame>
      <PageHeader
        description="Observed provider charges stay separate from optional counterfactual estimates."
        title="Costs"
      />
      <label className="field-control" htmlFor="cost-window">
        <span>Window</span>
        <select
          id="cost-window"
          onChange={(event) => setWindow(event.target.value as CostSummary["window"])}
          value={window}
        >
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
          <option value="90d">Last 90 days</option>
        </select>
      </label>
      {query.isPending ? <AsyncState status="loading" title="Loading observed usage" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {query.data ? (
        <>
          <section aria-labelledby="observed-cost-heading" className="priority-section">
            <div className="section-heading">
              <div>
                <h2 id="observed-cost-heading">Observed cost</h2>
                <p>Settled usage records reported by each workload category.</p>
              </div>
              <span className="telemetry">{query.data.currency}</span>
            </div>
            <dl className="cost-ledger">
              {categories.map(([category, amount]) => (
                <div key={category}>
                  <dt>{title(category)}</dt>
                  <dd className="telemetry">
                    {query.data.currency} {amount}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
          <section className="evidence-section">
            <h2>Counterfactual estimate</h2>
            {query.data.estimated_avoided_cost === null ? (
              <p>Not estimated. LoopGuard will not invent avoided cost without a supported comparison.</p>
            ) : (
              <p>
                Estimated avoided cost:{" "}
                <strong className="telemetry">
                  {query.data.currency} {query.data.estimated_avoided_cost}
                </strong>
              </p>
            )}
          </section>
        </>
      ) : null}
    </PageFrame>
  );
}

export function DevicesPage() {
  const query = useControlQuery<{ items: Device[] }>("/v1/devices");
  const [confirming, setConfirming] = useState<Device>();
  const [mutationState, setMutationState] = useState<"idle" | "saving" | "error">("idle");

  const revoke = async () => {
    if (!confirming || mutationState === "saving") return;
    setMutationState("saving");
    try {
      await controlClient().request<unknown>(`/v1/devices/${encodeURIComponent(confirming.id)}`, {
        method: "DELETE",
      });
      setConfirming(undefined);
      setMutationState("idle");
      await query.refetch();
    } catch {
      setMutationState("error");
    }
  };

  return (
    <PageFrame>
      <PageHeader
        description="Registered signing keys and their last authenticated observation. Private keys never appear here."
        title="Devices"
      />
      {query.isPending ? <AsyncState status="loading" title="Loading paired devices" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {query.data?.items.length === 0 ? (
        <AsyncState detail="Pair a device from the native app before approving remote actions." status="empty" title="No paired devices" />
      ) : null}
      {query.data?.items.length ? (
        <div className="resource-table-wrap">
          <table className="resource-table">
            <caption className="visually-hidden">Paired devices</caption>
            <thead>
              <tr>
                <th scope="col">Device</th>
                <th scope="col">Key</th>
                <th scope="col">Last seen</th>
                <th scope="col">State</th>
                <th scope="col">Action</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((device) => (
                <tr key={device.id}>
                  <th data-label="Device" scope="row">
                    {device.name}
                    <small>{shortID(device.id)}</small>
                  </th>
                  <td className="telemetry" data-label="Key">
                    {device.algorithm} · {shortID(device.key_id)}
                  </td>
                  <td data-label="Last seen">{formatDate(device.last_seen_at ?? device.created_at)}</td>
                  <td data-label="State">
                    <StatusLabel state={device.revoked_at ? "revoked" : "ready"} />
                  </td>
                  <td data-label="Action">
                    <button
                      className="button button--secondary"
                      disabled={Boolean(device.revoked_at)}
                      onClick={() => setConfirming(device)}
                      type="button"
                    >
                      {device.revoked_at ? "Revoked" : "Revoke"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {confirming ? (
        <section aria-labelledby="revoke-device-heading" className="confirmation-panel">
          <h2 id="revoke-device-heading">Revoke {confirming.name}?</h2>
          <p>Pending approvals from this device will fail closed. This cannot restore its private key.</p>
          <div>
            <button className="button" disabled={mutationState === "saving"} onClick={() => void revoke()} type="button">
              {mutationState === "saving" ? "Revoking device" : "Confirm revoke"}
            </button>
            <button className="button button--secondary" onClick={() => setConfirming(undefined)} type="button">
              Cancel
            </button>
          </div>
          {mutationState === "error" ? <p className="form-error">Device could not be revoked.</p> : null}
        </section>
      ) : null}
    </PageFrame>
  );
}

export function AuditPage() {
  const query = useControlQuery<{ items: AuditEntry[] }>("/v1/audit?limit=200");
  const [filter, setFilter] = useState("");
  const entries = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return query.data?.items ?? [];
    return (query.data?.items ?? []).filter((entry) =>
      [entry.action, entry.target_kind, entry.target_id, entry.actor, entry.result, entry.request_id]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(needle)),
    );
  }, [filter, query.data?.items]);

  return (
    <PageFrame>
      <PageHeader
        description="Append-only control history. Filter or export this view; audit records cannot be edited here."
        title="Audit"
      />
      <div className="toolbar">
        <label className="field-control" htmlFor="audit-filter">
          <span>Filter audit</span>
          <input
            id="audit-filter"
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Action, target, actor, result, or request ID"
            type="search"
            value={filter}
          />
        </label>
        <button className="button button--secondary" disabled={!entries.length} onClick={() => exportAudit(entries)} type="button">
          Export filtered CSV
        </button>
      </div>
      {query.isPending ? <AsyncState status="loading" title="Loading immutable audit" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {!query.isPending && !query.isError && entries.length === 0 ? (
        <AsyncState detail={filter ? "No records match the current filter." : "No control actions have been recorded."} status="empty" title="No audit records" />
      ) : null}
      {entries.length ? (
        <div className="resource-table-wrap">
          <table className="resource-table">
            <caption className="visually-hidden">Immutable audit records</caption>
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">Target</th>
                <th scope="col">Result</th>
                <th scope="col">Actor</th>
                <th scope="col">Recorded</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <th data-label="Action" scope="row">
                    {title(entry.action)}
                    <small>{shortID(entry.request_id ?? entry.id)}</small>
                  </th>
                  <td data-label="Target">{title(entry.target_kind)} · {shortID(entry.target_id)}</td>
                  <td data-label="Result"><StatusLabel state={entry.result ?? "recorded"} /></td>
                  <td data-label="Actor">{entry.actor ?? "System"}</td>
                  <td data-label="Recorded">{formatDate(entry.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </PageFrame>
  );
}

function exportAudit(entries: AuditEntry[]) {
  const rows = [
    ["id", "action", "target_kind", "target_id", "result", "actor", "request_id", "created_at"],
    ...entries.map((entry) => [
      entry.id,
      entry.action,
      entry.target_kind,
      entry.target_id,
      entry.result ?? "",
      entry.actor ?? "",
      entry.request_id ?? "",
      entry.created_at,
    ]),
  ];
  const csv = rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "loopguard-audit.csv";
  link.click();
  URL.revokeObjectURL(url);
}

function csvCell(value: string): string {
  const safe = /^[=+\-@]/.test(value) ? `'${value}` : value;
  return `"${safe.replaceAll('"', '""')}"`;
}

function title(value: string): string {
  return value
    .split(/[_-]/)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function shortID(value: string): string {
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-5)}` : value;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Not reported"
    : new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
