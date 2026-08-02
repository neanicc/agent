import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { PolicyEditor, type PolicyProfile } from "./policy-editor";

function managedProfile(): PolicyProfile {
  return {
    profile_version: 4,
    source_manifest_hash: "sha256:managed-defaults-v4",
    rules: [
      {
        id: "wcag-contrast",
        severity: "block",
        managed: true,
        source: "organization",
        precedence: "managed minimum",
        affected_capabilities: ["verification", "repair publication"],
      },
      {
        id: "loop-threshold",
        severity: "warn",
        managed: false,
        source: "profile",
        precedence: "profile override",
        affected_capabilities: ["loop intervention"],
      },
    ],
  };
}

test("cannot lower managed safety rule", async () => {
  const user = userEvent.setup();
  render(<PolicyEditor profile={managedProfile()} saveProfile={vi.fn()} />);

  await user.selectOptions(screen.getByLabelText("wcag-contrast severity"), "inform");

  expect(screen.getByText("Managed safety rules cannot be weakened")).toBeVisible();
  expect(screen.getByRole("button", { name: "Save policy" })).toBeDisabled();
});

test("shows source precedence and capability impact before save", async () => {
  const saveProfile = vi.fn().mockResolvedValue(managedProfile());
  const user = userEvent.setup();
  render(<PolicyEditor profile={managedProfile()} saveProfile={saveProfile} />);

  expect(screen.getByText("Organization")).toBeVisible();
  expect(screen.getByText("Managed minimum")).toBeVisible();
  expect(screen.getByText("Verification, repair publication")).toBeVisible();

  await user.selectOptions(screen.getByLabelText("loop-threshold severity"), "block");
  expect(screen.getByText("Loop intervention")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Save policy" }));

  expect(saveProfile).toHaveBeenCalledWith(
    expect.arrayContaining([
      expect.objectContaining({ id: "loop-threshold", severity: "block" }),
    ]),
  );
  expect(await screen.findByText(/Policy saved/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Save policy" })).toBeDisabled();
});
