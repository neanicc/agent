import React from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={{ flex: 1 }}>
      <Text style={{ color: theme.textDim, fontSize: 11, marginBottom: 2 }}>{label}</Text>
      <Text
        style={{ color: color || theme.text, fontSize: 18, fontWeight: "700", fontFamily: theme.mono }}
      >
        {value}
      </Text>
    </View>
  );
}

export function Meter({
  tokens,
  cost,
  judgeCost,
}: {
  tokens: number;
  cost: number;
  judgeCost?: number;
}) {
  return (
    <View
      style={{
        flexDirection: "row",
        backgroundColor: theme.surface,
        borderWidth: 1,
        borderColor: theme.border,
        borderRadius: theme.radius,
        padding: theme.space(4),
        gap: theme.space(3),
      }}
    >
      <Stat label="TOKENS" value={tokens.toLocaleString()} />
      <Stat label="TOTAL COST" value={`$${cost.toFixed(4)}`} color={theme.ok} />
      <Stat label="JUDGE" value={`$${(judgeCost ?? 0).toFixed(4)}`} color={theme.purple} />
    </View>
  );
}
