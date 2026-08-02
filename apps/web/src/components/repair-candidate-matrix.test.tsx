import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import {
  isFreshRepairCapability,
  RepairCandidateMatrix,
} from "./repair-candidate-matrix";


describe("repair capability", () => {
  test("requires a fresh ready server capability", () => {
    const now = new Date("2026-07-30T12:00:30Z");
    expect(
      isFreshRepairCapability(
        {
          status: "ready",
          observed_at: "2026-07-30T12:00:00Z",
          ttl_seconds: 60,
          features: {
            repair: {
              available: true,
              status: "ready",
              reason: "workflow_registered",
            },
          },
        },
        now,
      ),
    ).toBe(true);
    expect(
      isFreshRepairCapability(
        {
          status: "ready",
          observed_at: "2026-07-30T11:58:00Z",
          ttl_seconds: 60,
          features: { repair: { available: true, status: "ready" } },
        },
        now,
      ),
    ).toBe(false);
    expect(
      isFreshRepairCapability(
        {
          status: "ready",
          observed_at: "2026-07-30T12:00:00Z",
          ttl_seconds: 60,
          features: { repair: { available: false, status: "unavailable" } },
        },
        now,
      ),
    ).toBe(false);
  });
});

test("candidate matrix exposes exact checks contract impact and deterministic winner", () => {
  render(
    <RepairCandidateMatrix
      candidates={[
        {
          id: "candidate-1",
          strategy: "normalize ingestion boundary",
          changed_files: ["src/coordinates.py"],
          changed_lines: 8,
          evaluation: {
            replay: "passed",
            regression: "passed",
            security: "passed",
            contract_breaking: false,
            contract_changes: [],
          },
        },
        {
          id: "candidate-2",
          strategy: "coerce downstream",
          changed_files: ["src/export.py"],
          changed_lines: 19,
          evaluation: {
            replay: "passed",
            regression: "failed",
            security: "passed",
            contract_breaking: false,
          },
        },
      ]}
      ranking={{
        winning_candidate_id: "candidate-1",
        reason: "Smallest fully verified compatible patch.",
      }}
    />,
  );

  expect(screen.getByText("candidate-1")).toBeVisible();
  expect(screen.getByText("candidate-2")).toBeVisible();
  expect(screen.getByText("Smallest fully verified compatible patch.")).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Contract" })).toBeVisible();
  expect(screen.getByText("Winner")).toBeVisible();
});
