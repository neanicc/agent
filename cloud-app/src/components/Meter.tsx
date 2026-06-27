import React from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";

export function Meter({ tokens, cost }: { tokens: number; cost: number }) {
  return (
    <View style={{ flexDirection: "row", gap: theme.space(4) }}>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>
        <Text style={{ color: theme.text, fontWeight: "600" }}>{tokens}</Text> tokens
      </Text>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>
        <Text style={{ color: theme.text, fontWeight: "600" }}>${cost.toFixed(4)}</Text>
      </Text>
    </View>
  );
}
