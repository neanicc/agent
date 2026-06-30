import React from "react";
import { Pressable, SafeAreaView, Text, View } from "react-native";
import { theme } from "../theme";
import { AppIcon } from "./AppIcon";

export type TabKey = "run" | "history" | "autofix" | "allow";

const TABS = [
  { key: "run", label: "Run", symbol: "play.fill", fallback: "play" },
  { key: "history", label: "Agents", symbol: "clock.arrow.circlepath", fallback: "clock" },
  { key: "autofix", label: "Fixes", symbol: "wrench.and.screwdriver", fallback: "tool" },
  { key: "allow", label: "Allow", symbol: "checkmark.shield", fallback: "check-circle" },
] as const;

const tabStyle = {
  alignItems: "center" as const,
  flex: 1,
  gap: theme.space(1),
  justifyContent: "center" as const,
  minHeight: 54,
  paddingHorizontal: theme.space(1),
  position: "relative" as const,
};

function TabItem({
  item,
  active,
  onPress,
}: {
  item: (typeof TABS)[number];
  active: boolean;
  onPress: () => void;
}) {
  const color = active ? theme.accent : theme.textMuted;
  return (
    <Pressable
      accessibilityLabel={item.label}
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={({ pressed }) => [tabStyle, { opacity: pressed ? 0.62 : 1 }]}
    >
      <View
        style={{
          backgroundColor: active ? theme.accent : theme.bg2,
          height: 2,
          left: theme.space(5),
          position: "absolute",
          right: theme.space(5),
          top: 0,
        }}
      />
      <AppIcon
        color={color}
        fallback={item.fallback}
        size={19}
        symbol={item.symbol}
      />
      <Text
        numberOfLines={1}
        style={{
          color,
          fontFamily: active ? theme.bodySemibold : theme.bodyMedium,
          fontSize: 11,
          lineHeight: 14,
        }}
      >
        {item.label}
      </Text>
    </Pressable>
  );
}

export function TabBar({ active, onChange }: { active: TabKey; onChange: (t: TabKey) => void }) {
  return (
    <SafeAreaView
      style={{
        backgroundColor: theme.bg2,
        borderTopColor: theme.border,
        borderTopWidth: 1,
      }}
    >
      <View style={{ alignSelf: "center", flexDirection: "row", maxWidth: 720, width: "100%" }}>
        {TABS.map((item) => (
          <TabItem
            active={item.key === active}
            item={item}
            key={item.key}
            onPress={() => onChange(item.key)}
          />
        ))}
      </View>
    </SafeAreaView>
  );
}
