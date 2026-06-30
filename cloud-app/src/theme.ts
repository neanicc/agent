/*
 * Hallmark · genre: atmospheric · macrostructure: Workbench
 * theme: Midnight · design-system: design.md · designed-as-app
 * pre-emit critique: P5 H4 E4 S5 R5 V5
 */

export const theme = {
  bg: "#04080E",
  bg2: "#091019",
  surface: "#111923",
  surfaceHigh: "#18212C",
  surfaceInset: "#091019",
  border: "#2B343F",
  borderStrong: "#555F6B",
  text: "#EEF2F7",
  textDim: "#AAB2BB",
  textMuted: "#8B939D",
  accent: "#39A3FF",
  accentInk: "#050B16",
  focus: "#7ECCFF",
  ok: "#52C180",
  warn: "#E7B643",
  danger: "#F75D59",
  radius: 12,
  radiusLg: 16,
  radiusFull: 999,
  controlHeight: 48,
  hitTarget: 44,
  space: (n: number) => n * 4,
  display: "SpaceGrotesk_600SemiBold",
  displayBold: "SpaceGrotesk_700Bold",
  body: "IBMPlexSans_400Regular",
  bodyMedium: "IBMPlexSans_500Medium",
  bodySemibold: "IBMPlexSans_600SemiBold",
  mono: "IBMPlexMono_400Regular",
  monoMedium: "IBMPlexMono_500Medium",
  monoSemibold: "IBMPlexMono_600SemiBold",
} as const;

export type StatusKey = string;

export function statusColor(status: string): string {
  if (status === "awaiting_decision") return theme.warn;
  if (status === "error" || status === "stopped") return theme.danger;
  if (status === "completed") return theme.ok;
  return theme.accent;
}

export function statusLabel(status: string): string {
  if (status === "awaiting_decision") return "Needs review";
  if (status === "completed") return "Completed";
  if (status === "connecting") return "Connecting";
  if (status === "running") return "Running";
  if (status === "stopped") return "Stopped";
  if (status === "error") return "Error";
  return status.replace(/_/g, " ");
}
