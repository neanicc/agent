import React from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";
import { RunEvent } from "../runReducer";

export function EventRow({ event, index }: { event: RunEvent; index: number }) {
  const color = event.is_error ? theme.danger : theme.ok;
  const path = event.args?.path ?? "";
  return (
    <View
      style={{
        flexDirection: "row",
        gap: theme.space(2),
        paddingVertical: theme.space(2),
        borderBottomWidth: 1,
        borderBottomColor: theme.border,
      }}
    >
      <Text style={{ color: theme.textDim, width: 22, fontSize: 12 }}>{index + 1}</Text>
      <View style={{ flex: 1 }}>
        <Text style={{ color: theme.text, fontSize: 13 }}>
          <Text style={{ color: theme.accent }}>{event.tool}</Text>
          {path ? `("${path}")` : "()"}
        </Text>
        <Text numberOfLines={1} style={{ color, fontSize: 12, marginTop: 2 }}>
          {event.output}
        </Text>
      </View>
    </View>
  );
}
