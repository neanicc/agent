import { Platform } from "react-native";

export const theme = {
  bg: "#070A12",
  bg2: "#0B1020",
  surface: "#101827",
  surfaceHigh: "#172033",
  surfaceGlass: "rgba(23,32,51,0.86)",
  border: "rgba(148,163,184,0.18)",
  borderStrong: "rgba(148,163,184,0.34)",
  text: "#F8FAFC",
  textDim: "#94A3B8",
  textMuted: "#64748B",
  accent: "#38BDF8",
  accent2: "#6366F1",
  ok: "#34D399",
  warn: "#FBBF24",
  danger: "#FB7185",
  purple: "#C084FC",
  pink: "#F472B6",
  radius: 20,
  radiusLg: 28,
  space: (n: number) => n * 4,
  shadow: Platform.select({
    ios: {
      shadowColor: "#020617",
      shadowOpacity: 0.38,
      shadowRadius: 22,
      shadowOffset: { width: 0, height: 16 },
    },
    android: { elevation: 8 },
    default: {},
  }) as object,
  mono: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }) as string,
};

export type StatusKey = string;

export function statusColor(status: string): string {
  if (status === "awaiting_decision") return theme.warn;
  if (status === "error" || status === "stopped") return theme.danger;
  if (status === "completed") return theme.ok;
  return theme.accent;
}
