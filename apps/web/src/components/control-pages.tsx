"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { controlClient, useControlQuery } from "@/lib/api/use-control-query";
import { AsyncState } from "./async-state";
import { SessionTimeline } from "./session-timeline";
import { StatusLabel } from "./status-label";


type Collection = { items?: Array<Record<string, unknown>>; next_cursor?: string | null };

type ResourceListProps = {
  title: string;
  description: string;
  path: string;
  detailBase: string;
  emptyTitle: string;
  emptyDetail: string;
};

export function ResourceList({
  title,
  description,
  path,
  detailBase,
  emptyTitle,
  emptyDetail,
}: ResourceListProps) {
  const query = useControlQuery<Collection>(path);
  return (
    <PageFrame>
      <PageHeader description={description} title={title} />
      {query.isPending ? <AsyncState status="loading" title={`Loading ${title.toLowerCase()}`} /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {query.data?.items?.length === 0 ? (
        <AsyncState detail={emptyDetail} status="empty" title={emptyTitle} />
      ) : null}
      {query.data?.items && query.data.items.length > 0 ? (
        <ResourceTable detailBase={detailBase} items={query.data.items} />
      ) : null}
    </PageFrame>
  );
}

export function InboxPage() {
  const query = useQuery({
    queryKey: ["control", "inbox"],
    queryFn: async ({ signal }) => {
      const client = controlClient();
      const paths = ["/v1/sessions", "/v1/changes", "/v1/verifications", "/v1/hosts"] as const;
      const results = await Promise.all(
        paths.map((path) => client.request<Collection>(path, { signal })),
      );
      return paths.flatMap((path, index) =>
        (results[index].items ?? []).map((item) => ({ ...item, _source: path })),
      );
    },
  });
  const items = (query.data ?? []).map(toInboxItem).filter((item) => item.attention);
  const completions = (query.data ?? []).map(toInboxItem).filter((item) => !item.attention).slice(0, 3);

  return (
    <PageFrame>
      <PageHeader
        description="Finite, priority-ordered interventions from active runs and fresh host health."
        title="Inbox"
      />
      {query.isPending ? <AsyncState status="loading" title="Loading attention queue" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {!query.isPending && !query.isError && items.length === 0 ? (
        <section className="empty-inbox">
          <StatusLabel label="No action needed" state="ready" />
          <h2>No action needed</h2>
          <p>LoopGuard has no current regression, loop, stale host, or unresolved approval.</p>
          <p className="secondary-copy">Last synced {formatDate(new Date().toISOString())}</p>
          <Link className="button button--secondary" href="/runs">
            View recent runs
          </Link>
        </section>
      ) : null}
      {items.length > 0 ? (
        <section aria-labelledby="attention-heading" className="queue-section">
          <div className="section-heading">
            <div>
              <h2 id="attention-heading">Needs attention</h2>
              <p>Blocking and warning states, ordered before informational work.</p>
            </div>
            <span className="queue-count telemetry">{items.length}</span>
          </div>
          <ol className="attention-list">
            {[...items].sort(compareInbox).map((item) => (
              <li key={`${item.href}:${item.id}`}>
                <Link className="attention-row" href={item.href}>
                  <StatusLabel state={item.state} />
                  <span className="attention-row__main">
                    <strong>{item.title}</strong>
                    <span>{item.detail}</span>
                  </span>
                  <time dateTime={item.occurredAt}>{formatAge(item.occurredAt)}</time>
                </Link>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
      {completions.length > 0 ? (
        <section aria-labelledby="recent-heading" className="quiet-section">
          <h2 id="recent-heading">Recent completion</h2>
          <ul className="quiet-list">
            {completions.map((item) => (
              <li key={`${item.href}:${item.id}`}>
                <Link href={item.href}>{item.title}</Link>
                <StatusLabel state={item.state} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </PageFrame>
  );
}

export function HostsPage() {
  const query = useControlQuery<Collection>("/v1/hosts");
  return (
    <PageFrame>
      <PageHeader
        description="Pairing, daemon freshness, repository binding, trust, hook coverage, and adapter versions."
        title="Hosts & integrations"
      />
      {query.isPending ? <AsyncState status="loading" title="Checking host coverage" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {query.data?.items?.length === 0 ? (
        <section className="first-run-panel">
          <StatusLabel label="Setup required" state="pending_pairing" />
          <h2>Pair a host to observe your first run</h2>
          <p>
            Run setup on the developer machine. The web console never installs global hooks remotely.
          </p>
          <CopyCommand command="loopguard setup" />
          <Link href="/runs">View runs after setup</Link>
        </section>
      ) : null}
      {query.data?.items && query.data.items.length > 0 ? (
        <ResourceTable detailBase="/hosts" items={query.data.items} />
      ) : null}
    </PageFrame>
  );
}

export function RunDetail({ id }: { id: string }) {
  const query = useControlQuery<Record<string, unknown>>(`/v1/sessions/${encodeURIComponent(id)}`);
  const data = query.data;
  const proof = record(data?.verification ?? data?.proof);
  const action = record(data?.current_action ?? data?.action);
  const events = records(data?.events ?? data?.timeline);

  return (
    <PageFrame>
      <PageHeader
        back={{ href: "/runs", label: "Runs" }}
        description={text(data?.repository ?? data?.repo, "Repository unavailable")}
        title={text(data?.name ?? data?.title, `Run ${shortId(id)}`)}
      />
      {query.isPending ? <AsyncState status="loading" title="Loading run state and proof" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {data ? (
        <>
          <section aria-labelledby="run-state-heading" className="priority-section">
            <div className="section-heading">
              <div>
                <h2 id="run-state-heading">Current state</h2>
                <p>{text(data.summary, "The run has no additional summary.")}</p>
              </div>
              <StatusLabel state={text(data.state ?? data.status, "unknown")} />
            </div>
            <dl className="fact-grid">
              <Fact label="Phase" value={text(data.phase, "Unknown")} />
              <Fact label="Agent" value={text(data.agent, "Unknown")} />
              <Fact label="Model" value={text(data.model, "Unknown")} mono />
              <Fact label="Effort" value={text(data.effort, "Not reported")} />
              <Fact label="Observed cost" value={text(data.cost, "Not reported")} mono />
            </dl>
          </section>
          <VerificationSummary proof={proof} />
          <ActionSummary action={action} />
          <SessionTimeline initialEvents={events} sessionId={id} />
          <RawEvidence value={data} />
        </>
      ) : null}
    </PageFrame>
  );
}

export function ChangeDetail({ id }: { id: string }) {
  const query = useControlQuery<Record<string, unknown>>(`/v1/changes/${encodeURIComponent(id)}`);
  const data = query.data;
  const proof = record(data?.verification ?? data?.proof);
  return (
    <PageFrame>
      <PageHeader
        back={{ href: "/changes", label: "Changes" }}
        description={text(data?.repository ?? data?.repo, "Repository unavailable")}
        title={text(data?.summary ?? data?.title, `Change ${shortId(id)}`)}
      />
      {query.isPending ? <AsyncState status="loading" title="Loading change and provenance" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {data ? (
        <>
          <section className="priority-section">
            <div className="section-heading">
              <div>
                <h2>Change provenance</h2>
                <p>Actor and observation source precede the diff.</p>
              </div>
              <StatusLabel state={text(data.state ?? data.status, "observed")} />
            </div>
            <dl className="fact-grid">
              <Fact label="Actor" value={text(data.actor, "Unknown")} />
              <Fact label="Source" value={text(data.source ?? data.provenance, "Unknown")} />
              <Fact label="Branch" value={text(data.branch, "Not reported")} mono />
              <Fact label="Observed" value={formatDate(text(data.created_at ?? data.observed_at, ""))} />
            </dl>
          </section>
          <VerificationSummary proof={proof} />
          <section aria-labelledby="diff-heading" className="evidence-section">
            <h2 id="diff-heading">Diff evidence</h2>
            {typeof data.diff === "string" && data.diff ? (
              <pre className="diff-view">{data.diff}</pre>
            ) : (
              <p className="missing-evidence">No bounded diff artifact is available for this change.</p>
            )}
          </section>
          <RawEvidence value={data} />
        </>
      ) : null}
    </PageFrame>
  );
}

export function VerificationDetail({ id }: { id: string }) {
  const query = useControlQuery<Record<string, unknown>>(`/v1/verifications/${encodeURIComponent(id)}`);
  const data = query.data;
  const checks = records(data?.checks);
  return (
    <PageFrame>
      <PageHeader
        back={{ href: "/verification", label: "Verification" }}
        description="Deterministic checks appear before judge or critic evidence."
        title={text(data?.name ?? data?.title, `Verification ${shortId(id)}`)}
      />
      {query.isPending ? <AsyncState status="loading" title="Loading verification proof" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {data ? (
        <>
          <section className="priority-section">
            <div className="section-heading">
              <div>
                <h2>Verdict</h2>
                <p>{text(data.summary, "No verdict summary was provided.")}</p>
              </div>
              <StatusLabel state={text(data.verdict ?? data.state, "inconclusive")} />
            </div>
            <dl className="fact-grid">
              <Fact label="Command" value={text(data.command, "Not recorded")} mono />
              <Fact label="Exit status" value={text(data.exit_status, "Not reported")} mono />
              <Fact label="Rerun" value={booleanLabel(data.rerun_eligible, "Eligibility unknown")} />
              <Fact label="Proof signature" value={text(data.signature, "Not available")} mono />
            </dl>
          </section>
          {checks.length > 0 ? (
            <section className="evidence-section">
              <h2>Checks</h2>
              <ul className="check-list">
                {checks.map((check, index) => (
                  <li key={text(check.id ?? check.name, String(index))}>
                    <StatusLabel state={text(check.status, "unknown")} />
                    <span>{text(check.name, "Unnamed check")}</span>
                    {check.output ? <code>{text(check.output)}</code> : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <p className="missing-evidence">No deterministic check output was attached.</p>
          )}
          <RawEvidence value={data} />
        </>
      ) : null}
    </PageFrame>
  );
}

export function HostDetail({ id }: { id: string }) {
  const query = useControlQuery<Record<string, unknown>>(`/v1/hosts/${encodeURIComponent(id)}`);
  const data = query.data;
  const health = record(data?.health);
  const integrations = records(data?.integrations);
  return (
    <PageFrame>
      <PageHeader
        back={{ href: "/hosts", label: "Hosts & integrations" }}
        description="Freshness, trust, repository binding, hook coverage, and adapter compatibility."
        title={text(data?.name, `Host ${shortId(id)}`)}
      />
      {query.isPending ? <AsyncState status="loading" title="Loading host health" /> : null}
      {query.isError ? <AsyncState error={query.error} onRetry={() => void query.refetch()} status="error" /> : null}
      {data ? (
        <>
          <section className="priority-section">
            <div className="section-heading">
              <div>
                <h2>Host health</h2>
                <p>Destructive controls stay unavailable when this observation becomes stale.</p>
              </div>
              <StatusLabel state={text(health.status ?? data.state, "unknown")} />
            </div>
            <dl className="fact-grid">
              <Fact label="Last observed" value={formatDate(text(health.observed_at, ""))} />
              <Fact label="Freshness TTL" value={text(health.ttl_seconds, "Not reported")} mono />
              <Fact label="Adapter" value={text(data.adapter_version, "Unknown")} mono />
              <Fact label="Repository bound" value={booleanLabel(data.repository_bound)} />
            </dl>
          </section>
          <section className="evidence-section">
            <div className="section-heading">
              <div>
                <h2>Integration coverage</h2>
                <p>Plugin and fallback hooks are reported separately.</p>
              </div>
            </div>
            {integrations.length ? (
              <ul className="integration-list">
                {integrations.map((integration, index) => (
                  <li key={text(integration.id ?? integration.name, String(index))}>
                    <span>
                      <strong>{text(integration.name, "Integration")}</strong>
                      <small>{text(integration.source, "Source unknown")}</small>
                    </span>
                    <StatusLabel state={text(integration.state ?? integration.status, "unknown")} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="missing-evidence">No integration coverage report is available.</p>
            )}
          </section>
          <CopyCommand command="loopguard doctor" />
          <RawEvidence value={data} />
        </>
      ) : null}
    </PageFrame>
  );
}

function ResourceTable({ items, detailBase }: { items: Array<Record<string, unknown>>; detailBase: string }) {
  return (
    <div className="resource-table-wrap">
      <table className="resource-table">
        <caption className="visually-hidden">{detailBase.slice(1)} records</caption>
        <thead>
          <tr>
            <th scope="col">Item</th>
            <th scope="col">State</th>
            <th scope="col">Repository or source</th>
            <th scope="col">Observed</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => {
            const id = text(item.id, String(index));
            const state = resourceState(item);
            return (
              <tr key={id}>
                <th data-label="Item" scope="row">
                  <Link href={`${detailBase}/${encodeURIComponent(id)}`}>{resourceTitle(item, id)}</Link>
                  <small>{text(item.summary, shortId(id))}</small>
                </th>
                <td data-label="State">
                  <StatusLabel state={state} />
                </td>
                <td data-label="Repository or source">{text(item.repository ?? item.source, "Not reported")}</td>
                <td data-label="Observed">
                  <time dateTime={text(item.updated_at ?? item.created_at ?? item.observed_at, "")}>
                    {formatDate(text(item.updated_at ?? item.created_at ?? item.observed_at, ""))}
                  </time>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function VerificationSummary({ proof }: { proof: Record<string, unknown> }) {
  return (
    <section aria-labelledby="proof-heading" className="evidence-section">
      <div className="section-heading">
        <div>
          <h2 id="proof-heading">Verification proof</h2>
          <p>Deterministic result and provenance before raw output.</p>
        </div>
        <StatusLabel state={text(proof.verdict ?? proof.status, "inconclusive")} />
      </div>
      {Object.keys(proof).length ? (
        <dl className="fact-grid">
          <Fact label="Verdict" value={text(proof.verdict ?? proof.status, "Inconclusive")} />
          <Fact label="Command" value={text(proof.command, "Not reported")} mono />
          <Fact label="Artifact" value={text(proof.artifact_id, "Not attached")} mono />
          <Fact label="Observed" value={formatDate(text(proof.observed_at ?? proof.completed_at, ""))} />
        </dl>
      ) : (
        <p className="missing-evidence">No verification proof is attached to this item.</p>
      )}
    </section>
  );
}

function ActionSummary({ action }: { action: Record<string, unknown> }) {
  return (
    <section aria-labelledby="action-heading" className="action-summary">
      <div>
        <h2 id="action-heading">Safe action</h2>
        <p>
          {Object.keys(action).length
            ? text(action.effect, "Review the exact target and effect before approval.")
            : "No action currently requires review."}
        </p>
      </div>
      {Object.keys(action).length ? (
        <StatusLabel state={text(action.state, "reviewed")} />
      ) : (
        <StatusLabel label="No action" state="neutral" />
      )}
    </section>
  );
}

function CopyCommand({ command }: { command: string }) {
  const [state, setState] = useState<"idle" | "loading" | "success" | "error">("idle");
  const copy = async () => {
    setState("loading");
    try {
      await navigator.clipboard.writeText(command);
      setState("success");
      window.setTimeout(() => setState("idle"), 2_500);
    } catch {
      setState("error");
    }
  };
  return (
    <div className="copy-command" data-state={state}>
      <code>{command}</code>
      <button className="button button--secondary" disabled={state === "loading"} onClick={copy} type="button">
        {state === "loading" ? "Copying" : state === "success" ? "Copied" : state === "error" ? "Try copy again" : "Copy command"}
      </button>
      <span aria-live="polite" className="visually-hidden">
        {state === "success" ? "Command copied" : state === "error" ? "Command could not be copied" : ""}
      </span>
    </div>
  );
}

function PageFrame({ children }: Readonly<{ children: React.ReactNode }>) {
  return <div className="page-frame">{children}</div>;
}

function PageHeader({
  title,
  description,
  back,
}: {
  title: string;
  description: string;
  back?: { href: string; label: string };
}) {
  return (
    <header className="page-header">
      {back ? (
        <Link className="back-link" href={back.href}>
          <svg aria-hidden="true" viewBox="0 0 16 16">
            <path d="M9.75 3.25L5 8l4.75 4.75M5.5 8H13" />
          </svg>
          {back.label}
        </Link>
      ) : null}
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  );
}

function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "telemetry" : undefined} title={value}>
        {value}
      </dd>
    </div>
  );
}

function RawEvidence({ value }: { value: Record<string, unknown> }) {
  return (
    <details className="raw-evidence">
      <summary>Developer metadata</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function toInboxItem(value: Record<string, unknown>) {
  const source = text(value._source, "/v1/sessions");
  const id = text(value.id, "unknown");
  const state = resourceState(value);
  const severity = text(value.severity, state);
  const attention =
    value.requires_attention === true ||
    /block|warn|fail|regression|loop|stale|offline|pending|expired|revoked|inconclusive/.test(
      `${state} ${severity}`.toLowerCase(),
    );
  const base = source.includes("changes")
    ? "/changes"
    : source.includes("verifications")
      ? "/verification"
      : source.includes("hosts")
        ? "/hosts"
        : "/runs";
  return {
    id,
    href: `${base}/${encodeURIComponent(id)}`,
    state,
    severity,
    attention,
    title: resourceTitle(value, id),
    detail: text(value.summary ?? value.repository, "Open the item to inspect current proof."),
    occurredAt: text(value.updated_at ?? value.created_at ?? value.observed_at, new Date().toISOString()),
  };
}

function compareInbox(left: ReturnType<typeof toInboxItem>, right: ReturnType<typeof toInboxItem>) {
  const rank = (value: string) => (/block|fail|regression|offline|revoked/.test(value) ? 0 : 1);
  return rank(left.severity.toLowerCase()) - rank(right.severity.toLowerCase()) || right.occurredAt.localeCompare(left.occurredAt);
}

function resourceTitle(item: Record<string, unknown>, id: string): string {
  return text(item.name ?? item.title ?? item.summary, `Item ${shortId(id)}`);
}

function resourceState(item: Record<string, unknown>): string {
  const health = record(item.health);
  return text(item.state ?? item.status ?? item.verdict ?? health.status, "unknown");
}

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, fallback = ""): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function booleanLabel(value: unknown, fallback = "No"): string {
  return typeof value === "boolean" ? (value ? "Yes" : "No") : fallback;
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function formatDate(value: string): string {
  if (!value) return "Not reported";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatAge(value: string): string {
  const timestamp = new Date(value).valueOf();
  if (!Number.isFinite(timestamp)) return "Age unknown";
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "Now";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h` : `${Math.round(hours / 24)} d`;
}
