import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import {
  ActionSheet,
  type ActionReviewRequest,
  type ActionTransport,
} from "./action-sheet";

const NOW = new Date("2026-07-22T14:00:00Z");

function request(overrides: Partial<ActionReviewRequest> = {}): ActionReviewRequest {
  return {
    actionId: `act-${crypto.randomUUID()}`,
    state: "reviewed",
    target: { kind: "session", id: "auth-migration", label: "Session auth-migration" },
    effect: "Inject the verified correction and continue",
    risk: "Medium — changes the next agent turn",
    parametersHash: "sha256:8da6b7",
    expectedState: "Run remains paused at state version 14",
    expiresAt: "2026-07-22T14:05:00Z",
    approveLabel: "Approve correction",
    hostAvailable: true,
    ...overrides,
  };
}

function transport(overrides: Partial<ActionTransport> = {}): ActionTransport {
  return {
    approve: vi.fn().mockResolvedValue({ state: "queued" }),
    reject: vi.fn().mockResolvedValue({ state: "rejected", resolvedAt: "2026-07-22T14:01:00Z" }),
    read: vi.fn().mockResolvedValue({ state: "reviewed" }),
    ...overrides,
  };
}

describe("ActionSheet", () => {
  test("shows exact target effect risk expected state hash and expiry before submission", () => {
    render(<ActionSheet now={() => NOW} request={request()} transport={transport()} />);

    expect(screen.getByText("Session auth-migration")).toBeVisible();
    expect(screen.getByText("Inject the verified correction and continue")).toBeVisible();
    expect(screen.getByText("Medium — changes the next agent turn")).toBeVisible();
    expect(screen.getByText("sha256:8da6b7")).toBeVisible();
    expect(screen.getByText("Run remains paused at state version 14")).toBeVisible();
    expect(screen.getByText(/expires/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Approve correction" })).toBeEnabled();
  });

  test("expired request disables approval", () => {
    render(
      <ActionSheet
        now={() => NOW}
        request={request({ expiresAt: "2026-07-22T13:59:59Z" })}
        transport={transport()}
      />,
    );

    expect(screen.getByRole("button", { name: "Approval expired" })).toBeDisabled();
  });

  test("host-offline request cannot be confirmed", () => {
    render(<ActionSheet now={() => NOW} request={request({ hostAvailable: false })} transport={transport()} />);

    expect(screen.getByRole("button", { name: "Host offline" })).toBeDisabled();
  });

  test("double activation submits one action ID", async () => {
    const approve = vi.fn().mockImplementation(
      () => new Promise<{ state: "queued" }>((resolve) => setTimeout(() => resolve({ state: "queued" }), 1)),
    );
    const actionTransport = transport({ approve });
    const user = userEvent.setup();
    render(<ActionSheet now={() => NOW} request={request()} transport={actionTransport} />);
    const button = screen.getByRole("button", { name: "Approve correction" });

    await Promise.all([user.click(button), user.click(button)]);

    expect(approve).toHaveBeenCalledTimes(1);
  });

  test("HTTP acceptance remains queued and does not show a success receipt", async () => {
    const user = userEvent.setup();
    render(<ActionSheet now={() => NOW} request={request()} transport={transport()} />);

    await user.click(screen.getByRole("button", { name: "Approve correction" }));

    expect(await screen.findByRole("button", { name: "Queued for host" })).toBeDisabled();
    expect(screen.queryByText("Execution receipt")).not.toBeInTheDocument();
  });

  test("network ambiguity reads authoritative state without resubmitting", async () => {
    const approve = vi.fn().mockRejectedValue(new TypeError("network lost"));
    const read = vi.fn().mockResolvedValue({
      state: "executed" as const,
      resolvedAt: "2026-07-22T14:01:00Z",
      receiptId: "receipt-42",
    });
    const user = userEvent.setup();
    render(<ActionSheet now={() => NOW} request={request()} transport={transport({ approve, read })} />);

    await user.click(screen.getByRole("button", { name: "Approve correction" }));

    expect(await screen.findByText("Execution receipt")).toBeVisible();
    expect(screen.getByText("receipt-42")).toBeVisible();
    expect(approve).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledTimes(1);
  });
});
