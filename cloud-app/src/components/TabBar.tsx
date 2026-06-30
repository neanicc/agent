import React from "react";
import { Pressable, Text, View } from "react-native";
import { theme } from "../theme";

export type TabKey = "run" | "history" | "autofix" | "allow";

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: "run", label: "Run", icon: "✦" },
  { key: "history", label: "Agents", icon: "◎" },
  { key: "autofix", label: "Fixes", icon: "⚡" },
  { key: "allow", label: "Allow", icon: "✓" },
];

export function TabBar({ active, onChange }: { active: TabKey; onChange: (t: TabKey) => void }) {
  return (
    <View style={{ borderTopWidth: 1, borderTopColor: theme.border, backgroundColor: "rgba(7,10,18,0.94)", paddingHorizontal: theme.space(3), paddingBottom: theme.space(5), paddingTop: theme.space(2) }}>
      <View style={{ flexDirection: "row", backgroundColor: "rgba(15,23,42,0.82)", borderRadius: 999, borderWidth: 1, borderColor: theme.border, padding: theme.space(1), gap: theme.space(1) }}>
        {TABS.map((t) => {
          const on = t.key === active;
          return (
            <Pressable key={t.key} onPress={() => onChange(t.key)} style={{ flex: 1, alignItems: "center", paddingVertical: theme.space(2), borderRadius: 999, backgroundColor: on ? "rgba(56,189,248,0.16)" : "transparent" }}>
              <Text style={{ color: on ? theme.text : theme.textDim, fontSize: 11, fontWeight: on ? "900" : "700" }}>{t.icon} {t.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}
