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
        backgroundColor: event.tripped ? "rgba(245,158,11,0.08)" : undefined,
      }}
    >
      <Text style={{ color: theme.textDim, width: 22, fontSize: 12, fontFamily: theme.mono }}>
        {event.step ?? index + 1}
      </Text>
      <View style={{ flex: 1 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: theme.space(2) }}>
          {event.agent ? (
            <Text style={{ color: theme.purple, fontSize: 11, fontWeight: "700" }}>{event.agent}</Text>
          ) : null}
          <Text style={{ color: theme.text, fontSize: 13, fontFamily: theme.mono }}>
            <Text style={{ color: theme.accent }}>{event.tool}</Text>
            {path ? `("${path}")` : "()"}
          </Text>
          {event.tripped ? (
            <Text style={{ color: theme.warn, fontSize: 11, fontWeight: "700" }}>⚠ loop</Text>
          ) : null}
        </View>
        <Text numberOfLines={1} style={{ color, fontSize: 12, marginTop: 2 }}>
          {event.output}
        </Text>
      </View>
    </View>
  );
}
