import React from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";
import { RunEvent } from "../runReducer";
import { AppIcon } from "./AppIcon";

export function EventRow({ event, index }: { event: RunEvent; index: number }) {
  const color = event.is_error ? theme.danger : theme.ok;
  const path = event.args?.path ?? "";
  return (
    <View
      style={{
        backgroundColor: event.tripped ? theme.surfaceHigh : theme.surface,
        borderBottomColor: theme.border,
        borderBottomWidth: 1,
        flexDirection: "row",
        gap: theme.space(2),
        paddingHorizontal: theme.space(3),
        paddingVertical: theme.space(3),
      }}
    >
      <Text
        style={{
          color: theme.textMuted,
          fontFamily: theme.monoMedium,
          fontSize: 12,
          lineHeight: 18,
          width: 24,
        }}
      >
        {event.step ?? index + 1}
      </Text>
      <View style={{ flex: 1, gap: theme.space(1), minWidth: 0 }}>
        <View style={{ alignItems: "center", flexDirection: "row", gap: theme.space(2) }}>
          {event.agent ? (
            <Text
              numberOfLines={1}
              style={{
                color: theme.textDim,
                fontFamily: theme.bodySemibold,
                fontSize: 12,
                lineHeight: 16,
                maxWidth: 92,
              }}
            >
              {event.agent}
            </Text>
          ) : null}
          <Text
            numberOfLines={1}
            style={{
              color: theme.text,
              flex: 1,
              fontFamily: theme.mono,
              fontSize: 13,
              lineHeight: 18,
            }}
          >
            <Text style={{ color: theme.accent, fontFamily: theme.monoMedium }}>{event.tool}</Text>
            {path ? `("${path}")` : "()"}
          </Text>
          {event.tripped ? (
            <View style={{ alignItems: "center", flexDirection: "row", gap: theme.space(1) }}>
              <AppIcon
                color={theme.warn}
                fallback="alert-triangle"
                size={14}
                symbol="exclamationmark.triangle.fill"
              />
              <Text style={{ color: theme.warn, fontFamily: theme.bodySemibold, fontSize: 12 }}>
                Loop
              </Text>
            </View>
          ) : null}
        </View>
        <View style={{ alignItems: "flex-start", flexDirection: "row", gap: theme.space(2) }}>
          <AppIcon
            color={color}
            fallback={event.is_error ? "x-circle" : "check-circle"}
            size={14}
            symbol={event.is_error ? "xmark.circle.fill" : "checkmark.circle.fill"}
          />
          <Text
            numberOfLines={2}
            style={{
              color: theme.textDim,
              flex: 1,
              fontFamily: theme.body,
              fontSize: 13,
              lineHeight: 18,
            }}
          >
          {event.output}
          </Text>
        </View>
      </View>
    </View>
  );
}
