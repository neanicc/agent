import React, { useEffect, useState } from "react";
import { RefreshControl, ScrollView, Text, View } from "react-native";
import { theme } from "../theme";
import { AutoFixEntry, LoopGuardClient } from "../client";

export function AutoFixScreen({ client }: { client: LoopGuardClient }) {
  const [items, setItems] = useState<AutoFixEntry[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const load = () => client.autofixes().then(setItems).catch(() => {});
  useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [client]);

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: theme.bg }}
      contentContainerStyle={{ padding: theme.space(5), paddingTop: theme.space(14), gap: theme.space(3) }}
      refreshControl={
        <RefreshControl refreshing={refreshing} tintColor={theme.accent}
          onRefresh={() => { setRefreshing(true); load().finally(() => setRefreshing(false)); }} />
      }
    >
      <Text style={{ color: theme.text, fontSize: 24, fontWeight: "800" }}>Auto-fixes</Text>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>
        Loops the guard caught and resolved on its own, with no human in the loop.
      </Text>
      {items.length === 0 ? (
        <Text style={{ color: theme.textDim, fontSize: 13, marginTop: theme.space(2) }}>
          Nothing yet. Run a project in <Text style={{ color: theme.text }}>auto</Text> mode.
        </Text>
      ) : null}
      {items.map((a, i) => (
        <View
          key={i}
          style={{
            backgroundColor: theme.surface,
            borderWidth: 1,
            borderLeftWidth: 3,
            borderColor: theme.border,
            borderLeftColor: a.terminated ? theme.danger : theme.purple,
            borderRadius: theme.radius,
            padding: theme.space(4),
            gap: theme.space(1),
          }}
        >
          <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
            <Text style={{ color: theme.text, fontWeight: "700", fontSize: 13 }}>{a.label}</Text>
            <Text style={{ color: a.terminated ? theme.danger : theme.purple, fontSize: 12, fontWeight: "700" }}>
              {a.terminated ? "terminated" : "fixed"} · {a.detector}
            </Text>
          </View>
          {a.judge_reasoning ? (
            <Text style={{ color: theme.textDim, fontSize: 12 }}>{a.judge_reasoning}</Text>
          ) : null}
          {a.applied_fix ? (
            <Text style={{ color: theme.text, fontSize: 12 }}>↳ {a.applied_fix}</Text>
          ) : null}
        </View>
      ))}
    </ScrollView>
  );
}
