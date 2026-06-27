import React from "react";
import { Pressable, Text, View } from "react-native";
import { theme } from "../theme";

export type TabKey = "run" | "history" | "autofix" | "allow";

const TABS: { key: TabKey; label: string }[] = [
  { key: "run", label: "Run" },
  { key: "history", label: "Agents" },
  { key: "autofix", label: "Auto-fixes" },
  { key: "allow", label: "Allowlist" },
];

export function TabBar({ active, onChange }: { active: TabKey; onChange: (t: TabKey) => void }) {
  return (
    <View
      style={{
        flexDirection: "row",
        borderTopWidth: 1,
        borderTopColor: theme.border,
        backgroundColor: theme.surface,
        paddingBottom: theme.space(6),
        paddingTop: theme.space(2),
      }}
    >
      {TABS.map((t) => {
        const on = t.key === active;
        return (
          <Pressable
            key={t.key}
            onPress={() => onChange(t.key)}
            style={{ flex: 1, alignItems: "center", paddingVertical: theme.space(2) }}
          >
            <Text style={{ color: on ? theme.accent : theme.textDim, fontSize: 13, fontWeight: on ? "800" : "500" }}>
              {t.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}
