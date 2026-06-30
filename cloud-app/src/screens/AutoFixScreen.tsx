import React, { useEffect, useState } from "react";
import { RefreshControl, Text, View } from "react-native";
import { theme } from "../theme";
import {
  ActionButton,
  EmptyState,
  PageHeader,
  Screen,
  Section,
  StatusBadge,
} from "../components/ui";
import { AutoFixEntry, LoopGuardClient } from "../client";
import { AppIcon } from "../components/AppIcon";

export function AutoFixScreen({
  client,
  onLaunch,
}: {
  client: LoopGuardClient;
  onLaunch: () => void;
}) {
  const [items, setItems] = useState<AutoFixEntry[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const load = () => client.autofixes().then(setItems).catch(() => {});
  useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [client]);

  return (
    <Screen
      refreshControl={
        <RefreshControl refreshing={refreshing} tintColor={theme.accent}
          onRefresh={() => { setRefreshing(true); load().finally(() => setRefreshing(false)); }} />
      }
    >
      <PageHeader
        subtitle="Corrections LoopGuard applied without waiting for a human decision."
        title="Auto-fixes"
      />
      {items.length === 0 ? (
        <Section>
          <EmptyState
            action={
              <ActionButton label="Open Run" onPress={onLaunch} variant="secondary" />
            }
            body="Launch a project in Auto-fix mode to record corrections here."
            fallback="tool"
            symbol="wrench.and.screwdriver"
            title="No automatic corrections"
          />
        </Section>
      ) : null}
      {items.length > 0 ? (
        <Section flush title="Repair ledger">
          {items.map((item, index) => {
            const color = item.terminated ? theme.danger : theme.ok;
            return (
              <View
                key={`${item.run_id}-${item.step}-${index}`}
                style={{
                  borderTopColor: index === 0 ? theme.surface : theme.border,
                  borderTopWidth: index === 0 ? 0 : 1,
                  gap: theme.space(2),
                  padding: theme.space(4),
                }}
              >
                <View style={{ alignItems: "center", flexDirection: "row", gap: theme.space(2) }}>
                  <AppIcon
                    color={color}
                    fallback={item.terminated ? "x-circle" : "check-circle"}
                    size={18}
                    symbol={item.terminated ? "xmark.circle.fill" : "checkmark.circle.fill"}
                  />
                  <Text
                    numberOfLines={1}
                    style={{
                      color: theme.text,
                      flex: 1,
                      fontFamily: theme.bodySemibold,
                      fontSize: 15,
                      lineHeight: 19,
                    }}
                  >
                    {item.label}
                  </Text>
                  <StatusBadge color={color} label={item.terminated ? "Stopped" : "Applied"} />
                </View>
                <Text
                  style={{
                    color: theme.textMuted,
                    fontFamily: theme.mono,
                    fontSize: 12,
                    lineHeight: 17,
                  }}
                >
                  Step {item.step} · {item.detector || "detector"}
                </Text>
                {item.judge_reasoning ? (
                  <Text
                    style={{
                      color: theme.textDim,
                      fontFamily: theme.body,
                      fontSize: 14,
                      lineHeight: 21,
                    }}
                  >
                    {item.judge_reasoning}
                  </Text>
                ) : null}
                {item.applied_fix ? (
                  <View style={{ flexDirection: "row", gap: theme.space(2) }}>
                    <AppIcon
                      color={theme.accent}
                      fallback="corner-down-right"
                      size={15}
                      symbol="arrow.turn.down.right"
                    />
                    <Text
                      style={{
                        color: theme.text,
                        flex: 1,
                        fontFamily: theme.bodyMedium,
                        fontSize: 13,
                        lineHeight: 19,
                      }}
                    >
                      {item.applied_fix}
                    </Text>
                  </View>
                ) : null}
              </View>
            );
          })}
        </Section>
      ) : null}
    </Screen>
  );
}
