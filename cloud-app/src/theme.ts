import { Platform } from "react-native";

export const theme = {
  bg: "#0A0A0A",
  surface: "#141414",
  surfaceHigh: "#1C1C1F",
  border: "#262629",
  text: "#EDEDED",
  textDim: "#8A8A8F",
  accent: "#3B82F6",
  ok: "#22C55E",
  warn: "#F59E0B",
  danger: "#EF4444",
  purple: "#A855F7",
  radius: 14,
  space: (n: number) => n * 4,
  // System monospace — no font download needed, used for meters and code-ish values.
  mono: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }) as string,
};

export type StatusKey = string;

export function statusColor(status: string): string {
  if (status === "awaiting_decision") return theme.warn;
  if (status === "error" || status === "stopped") return theme.danger;
  if (status === "completed") return theme.ok;
  return theme.accent; // running / connecting
}
