import { expect, test } from "@playwright/test";

import { ids, mockControlApi } from "./fixtures";

test("expired action shows immutable review details and cannot submit", async ({ page }) => {
  await mockControlApi(page, {
    sessionOverride: {
      current_action: {
        action_id: "act-expired",
        state: "expired",
        target: { kind: "session", target_id: "auth-migration" },
        target_label: "Session auth-migration",
        effect: "Inject the verified correction and continue",
        risk: "Medium — changes the next agent turn",
        parameters_hash: "sha256:8da6b7",
        expected_state: "Run remains paused at state version 14",
        expires_at: "2026-07-22T13:59:59Z",
        approve_label: "Approve correction",
        host_status: "ready",
      },
    },
  });

  await page.goto(`/runs/${ids.session}`);

  await expect(page.getByRole("heading", { name: "Review safe action" })).toBeVisible();
  await expect(page.getByText("Session auth-migration", { exact: true })).toBeVisible();
  await expect(page.getByText("Inject the verified correction and continue", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approval expired" })).toBeDisabled();
  await expect(page.getByText("Execution receipt", { exact: true })).toHaveCount(0);
});

test("revoked and offline actions cannot be confirmed", async ({ page }) => {
  await mockControlApi(page, {
    sessionOverride: {
      current_action: {
        action_id: "act-revoked",
        state: "revoked",
        target: { kind: "session", target_id: "auth-migration" },
        effect: "Continue this run once",
        risk: "Medium",
        parameters_hash: "sha256:revoked",
        expected_state: "Paused",
        expires_at: "2099-07-22T14:05:00Z",
        host_status: "offline",
      },
    },
  });

  await page.goto(`/runs/${ids.session}`);

  await expect(page.getByRole("button", { name: "Approval revoked" })).toBeDisabled();
});
