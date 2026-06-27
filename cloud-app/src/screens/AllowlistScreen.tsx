import React, { useEffect, useState } from "react";
import { RefreshControl, ScrollView, Text, View } from "react-native";
import { theme } from "../theme";
import { AllowlistEntry, LoopGuardClient } from "../client";

export function AllowlistScreen({ client }: { client: LoopGuardClient }) {
  const [items, setItems] = useState<AllowlistEntry[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const load = () => client.allowlist().then(setItems).catch(() => {});
  useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [client]);

  const tools = Array.from(new Set(items.flatMap((e) => e.tools)));

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: theme.bg }}
      contentContainerStyle={{ padding: theme.space(5), paddingTop: theme.space(14), gap: theme.space(3) }}
      refreshControl={
        <RefreshControl refreshing={refreshing} tintColor={theme.accent}
          onRefresh={() => { setRefreshing(true); load().finally(() => setRefreshing(false)); }} />
      }
    >
      <Text style={{ color: theme.text, fontSize: 24, fontWeight: "800" }}>Allowlist</Text>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>
        Tools you told LoopGuard to stop flagging. The guard waves these through for the rest of
        the run they were approved in.
      </Text>

      {tools.length > 0 ? (
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: theme.space(2), marginTop: theme.space(1) }}>
          {tools.map((t) => (
            <Text
              key={t}
              style={{
                color: theme.purple,
                borderColor: theme.purple,
                borderWidth: 1,
                borderRadius: 999,
                paddingHorizontal: theme.space(3),
                paddingVertical: theme.space(1),
                fontSize: 13,
                fontFamily: theme.mono,
                overflow: "hidden",
              }}
            >
              {t}
            </Text>
          ))}
        </View>
      ) : null}

      {items.length === 0 ? (
        <Text style={{ color: theme.textDim, fontSize: 13, marginTop: theme.space(2) }}>
          Nothing allowlisted yet. In a flagged loop, tap <Text style={{ color: theme.text }}>Allow tool</Text>.
        </Text>
      ) : null}

      {items.map((e, i) => (
        <View
          key={i}
          style={{
            backgroundColor: theme.surface,
            borderWidth: 1,
            borderColor: theme.border,
            borderRadius: theme.radius,
            padding: theme.space(4),
            gap: theme.space(1),
          }}
        >
          <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
            <Text style={{ color: theme.text, fontWeight: "700", fontSize: 13 }}>
              {e.tools.join(", ")}
            </Text>
            <Text style={{ color: theme.textDim, fontSize: 12 }}>{e.label}</Text>
          </View>
          {e.reason ? <Text style={{ color: theme.textDim, fontSize: 12 }}>{e.reason}</Text> : null}
        </View>
      ))}
    </ScrollView>
  );
}
