import React from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";

function Stat({
  label,
  value,
  color = theme.text,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <View accessibilityLabel={`${label}: ${value}`} style={{ flex: 1, gap: theme.space(1) }}>
      <Text
        style={{
          color: theme.textMuted,
          fontFamily: theme.bodyMedium,
          fontSize: 12,
          lineHeight: 16,
        }}
      >
        {label}
      </Text>
      <Text
        style={{
          color,
          fontFamily: theme.bodySemibold,
          fontSize: 17,
          fontVariant: ["tabular-nums"],
          lineHeight: 22,
        }}
      >
        {value}
      </Text>
    </View>
  );
}

export function Meter({ tokens, cost, judgeCost }: { tokens: number; cost: number; judgeCost?: number }) {
  return (
    <View
      style={{
        backgroundColor: theme.surface,
        borderColor: theme.border,
        borderRadius: theme.radiusLg,
        borderWidth: 1,
        flexDirection: "row",
        gap: theme.space(3),
        padding: theme.space(4),
      }}
    >
      <Stat label="Tokens" value={tokens.toLocaleString()} />
      <View style={{ backgroundColor: theme.border, width: 1 }} />
      <Stat color={theme.ok} label="Run cost" value={`$${cost.toFixed(4)}`} />
      <View style={{ backgroundColor: theme.border, width: 1 }} />
      <Stat label="Judge" value={`$${(judgeCost ?? 0).toFixed(4)}`} />
    </View>
  );
}
