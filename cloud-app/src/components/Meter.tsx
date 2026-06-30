import React from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";
import { Card } from "./ui";

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return <View style={{ flex: 1 }}><Text style={{ color: theme.textDim, fontSize: 10, marginBottom: 4, fontWeight: "800", letterSpacing: 1 }}>{label}</Text><Text style={{ color: color || theme.text, fontSize: 18, fontWeight: "900", fontFamily: theme.mono }}>{value}</Text></View>;
}

export function Meter({ tokens, cost, judgeCost }: { tokens: number; cost: number; judgeCost?: number }) {
  return <Card style={{ padding: theme.space(4), borderRadius: theme.radius }}><View style={{ flexDirection: "row", gap: theme.space(3) }}><Stat label="TOKENS" value={tokens.toLocaleString()} /><Stat label="TOTAL" value={`$${cost.toFixed(4)}`} color={theme.ok} /><Stat label="JUDGE" value={`$${(judgeCost ?? 0).toFixed(4)}`} color={theme.purple} /></View></Card>;
}
