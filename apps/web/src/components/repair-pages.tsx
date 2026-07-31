"use client";

import type { components } from "@/lib/api/generated";
import { useControlQuery } from "@/lib/api/use-control-query";
import Link from "next/link";

import { AsyncState } from "./async-state";
import { PageFrame, PageHeader } from "./control-pages";
import {
  isFreshRepairCapability,
  RepairCandidateMatrix,
  type RepairCapabilities,
} from "./repair-candidate-matrix";
import { StatusLabel } from "./status-label";


type RepairPageResponse = components["schemas"]["RepairPage"];
type RepairDetailResponse = components["schemas"]["RepairDetail"];
type HostCollection = { items?: Array<{ id?: string }> };

export function RepairListPage() {
  const capability = useRepairCapability();
  const repairs = useControlQuery<RepairPageResponse>("/v1/repairs", {
    enabled: capability.ready,
  });
  return (
    <PageFrame>
      <PageHeader
        description="Reproduced pipeline failures, isolated candidate evidence, and draft-only publication."
        title="Repairs"
      />
      {capability.pending ? (
        <AsyncState status="loading" title="Checking repair eligibility" />
      ) : null}
      {!capability.pending && !capability.ready ? (
        <AsyncState
          detail="A fresh host and registered repair workflow are required. No repair navigation or action is enabled from stale capability data."
          status="empty"
          title="Repairs aren’t available"
        />
      ) : null}
      {repairs.isPending && capability.ready ? (
        <AsyncState status="loading" title="Loading pipeline repairs" />
      ) : null}
      {repairs.isError ? (
        <AsyncState
          error={repairs.error}
          onRetry={() => void repairs.refetch()}
          status="error"
        />
      ) : null}
      {repairs.data?.items.length === 0 ? (
        <AsyncState
          detail="No eligible pipeline failure has entered the repair workflow for this organization."
          status="empty"
          title="No repair evidence yet"
        />
      ) : null}
      {repairs.data?.items.length ? (
        <div className="resource-table-wrap">
          <table className="resource-table">
            <caption className="visually-hidden">
              Evidence-backed pipeline repairs
            </caption>
            <thead>
              <tr>
                <th scope="col">Repair</th>
                <th scope="col">State</th>
                <th scope="col">Winning candidate</th>
                <th scope="col">Updated</th>
              </tr>
            </thead>
            <tbody>
              {repairs.data.items.map((repair) => (
                <tr key={repair.id}>
                  <th data-label="Repair" scope="row">
                    <Link href={`/repairs/${encodeURIComponent(repair.id)}`}>
                      Pipeline repair {shortId(repair.id)}
                    </Link>
                    <small className="telemetry">
                      {shortFingerprint(repair.failure_fingerprint)}
                    </small>
                  </th>
                  <td data-label="State">
                    <StatusLabel state={repair.state} />
                  </td>
                  <td data-label="Winning candidate">
                    {repair.winning_candidate_id ?? "Not ranked"}
                  </td>
                  <td data-label="Updated">
                    <time dateTime={repair.updated_at}>
                      {formatDate(repair.updated_at)}
                    </time>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </PageFrame>
  );
}

export function RepairDetailPage({ id }: { id: string }) {
  const capability = useRepairCapability();
  const repair = useControlQuery<RepairDetailResponse>(
    `/v1/repairs/${encodeURIComponent(id)}`,
    { enabled: capability.ready },
  );
  const data = repair.data;
  const reproduction = record(data?.reproduction);
  const publication = record(data?.publication);
  return (
    <PageFrame>
      <PageHeader
        back={{ href: "/repairs", label: "Repairs" }}
        description="Sanitized proof and deterministic checks; raw fixtures and credentials are never returned."
        title={`Repair ${shortId(id)}`}
      />
      {capability.pending ? (
        <AsyncState status="loading" title="Checking repair capability" />
      ) : null}
      {!capability.pending && !capability.ready ? (
        <AsyncState
          detail="The repair capability is unavailable, stale, or revoked. Refresh host health before reviewing evidence."
          status="stale"
          title="Repair access is locked"
        />
      ) : null}
      {repair.isPending && capability.ready ? (
        <AsyncState status="loading" title="Loading repair proof" />
      ) : null}
      {repair.isError ? (
        <AsyncState
          error={repair.error}
          onRetry={() => void repair.refetch()}
          status="error"
        />
      ) : null}
      {data ? (
        <>
          <section className="priority-section">
            <div className="section-heading">
              <div>
                <h2>Workflow state</h2>
                <p>
                  Publication is gated to this exact state version and hash.
                </p>
              </div>
              <StatusLabel state={data.state} />
            </div>
            <dl className="fact-grid">
              <Fact label="Repository" value={data.repository_id} mono />
              <Fact
                label="Failure fingerprint"
                value={shortFingerprint(data.failure_fingerprint)}
                mono
              />
              <Fact label="State version" value={String(data.state_version)} mono />
              <Fact label="State hash" value={shortFingerprint(data.state_hash)} mono />
              <Fact label="Updated" value={formatDate(data.updated_at)} />
            </dl>
          </section>

          <section className="evidence-section">
            <div className="section-heading">
              <div>
                <h2>Reproduction proof</h2>
                <p>
                  Candidate generation stays disabled until the original
                  fingerprint reproduces in isolation.
                </p>
              </div>
              <StatusLabel
                state={
                  reproduction.status === "reproduced" ||
                  reproduction.reproduced === true
                    ? "passed"
                    : "inconclusive"
                }
              />
            </div>
            <dl className="fact-grid">
              <Fact
                label="Result"
                value={text(
                  reproduction.status,
                  reproduction.reproduced === true
                    ? "Reproduced"
                    : "Not reproduced",
                )}
              />
              <Fact
                label="Attempts"
                value={text(reproduction.attempts, "Not reported")}
                mono
              />
              <Fact
                label="Artifact"
                value={text(
                  reproduction.artifact_id ??
                    reproduction.output_artifact_id,
                  "Not reported",
                )}
                mono
              />
              <Fact
                label="Assurance"
                value={text(reproduction.assurance, "Not reported")}
              />
            </dl>
          </section>

          <RepairCandidateMatrix
            candidates={data.candidates}
            ranking={data.ranking}
          />

          <section className="evidence-section repair-publication">
            <div className="section-heading">
              <div>
                <h2>Publication</h2>
                <p>
                  LoopGuard can create a draft pull request. It cannot merge or
                  deploy it.
                </p>
              </div>
              <StatusLabel
                state={text(publication.status, "not_started")}
              />
            </div>
            <dl className="fact-grid">
              <Fact
                label="Status"
                value={text(publication.status, "Not started")}
              />
              <Fact
                label="Draft pull request"
                value={text(
                  publication.pull_request_url ?? publication.url,
                  "Not published",
                )}
                mono
              />
              <Fact
                label="Publication artifact"
                value={text(publication.artifact_id, "Not reported")}
                mono
              />
            </dl>
            {data.state === "awaiting_publication" ? (
              <div className="publication-gate">
                <strong>Explicit approval required</strong>
                <p>
                  Review and sign the current publication action from a paired
                  device. A lost response is reconciled by action ID; evaluation
                  and publication are never silently repeated.
                </p>
                <Link className="button button--secondary" href="/devices">
                  Review paired devices
                </Link>
              </div>
            ) : null}
          </section>

          <section className="evidence-section">
            <h2>Rollback</h2>
            <p>{data.rollback}</p>
          </section>
        </>
      ) : null}
    </PageFrame>
  );
}

function useRepairCapability() {
  const hosts = useControlQuery<HostCollection>("/v1/hosts");
  const hostId = hosts.data?.items?.[0]?.id;
  const capability = useControlQuery<RepairCapabilities>(
    `/v1/capabilities?host_id=${encodeURIComponent(hostId ?? "")}`,
    { enabled: Boolean(hostId) },
  );
  return {
    pending: hosts.isPending || (Boolean(hostId) && capability.isPending),
    ready: isFreshRepairCapability(capability.data),
  };
}

function Fact({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "telemetry" : undefined} title={value}>
        {value}
      </dd>
    </div>
  );
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown, fallback = ""): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function shortFingerprint(value: string): string {
  const normalized = value.replace(/^sha256:/, "");
  return normalized.length > 20
    ? `sha256:${normalized.slice(0, 12)}…${normalized.slice(-6)}`
    : value;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Not reported"
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}
