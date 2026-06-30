import React, { useEffect, useState } from "react";
import { RefreshControl, Text, View } from "react-native";
import { theme } from "../theme";
import { ActionButton, EmptyState, PageHeader, Screen, Section } from "../components/ui";
import { AllowlistEntry, LoopGuardClient } from "../client";
import { AppIcon } from "../components/AppIcon";

export function AllowlistScreen({
  client,
  onLaunch,
}: {
  client: LoopGuardClient;
  onLaunch: () => void;
}) {
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
    <Screen
      refreshControl={
        <RefreshControl refreshing={refreshing} tintColor={theme.accent}
          onRefresh={() => { setRefreshing(true); load().finally(() => setRefreshing(false)); }} />
      }
    >
      <PageHeader
        subtitle="Tools you approved so repeated use no longer interrupts a run."
        title="Allowlist"
      />

      {tools.length > 0 ? (
        <Section title="Allowed tools">
          {tools.map((tool, index) => (
            <View
              key={tool}
              style={{
                alignItems: "center",
                borderTopColor: index === 0 ? theme.surface : theme.border,
                borderTopWidth: index === 0 ? 0 : 1,
                flexDirection: "row",
                gap: theme.space(3),
                minHeight: theme.hitTarget,
              }}
            >
              <AppIcon
                color={theme.ok}
                fallback="check-circle"
                size={17}
                symbol="checkmark.circle.fill"
              />
              <Text
                numberOfLines={1}
                style={{
                  color: theme.text,
                  flex: 1,
                  fontFamily: theme.monoMedium,
                  fontSize: 14,
                  lineHeight: 19,
                }}
              >
                {tool}
              </Text>
            </View>
          ))}
        </Section>
      ) : null}

      {items.length === 0 ? (
        <Section>
          <EmptyState
            action={
              <ActionButton label="Open Run" onPress={onLaunch} variant="secondary" />
            }
            body="When a reviewed loop is safe, choose Allow tool to add it here."
            fallback="check-circle"
            symbol="checkmark.shield"
            title="No tools approved"
          />
        </Section>
      ) : null}

      {items.length > 0 ? (
        <Section flush title="Approval history">
          {items.map((entry, index) => (
            <View
              key={`${entry.run_id}-${entry.ts}-${index}`}
              style={{
                borderTopColor: index === 0 ? theme.surface : theme.border,
                borderTopWidth: index === 0 ? 0 : 1,
                gap: theme.space(2),
                padding: theme.space(4),
              }}
            >
              <View style={{ alignItems: "center", flexDirection: "row", gap: theme.space(2) }}>
                <AppIcon
                  color={theme.ok}
                  fallback="shield"
                  size={17}
                  symbol="checkmark.shield"
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
                  {entry.tools.join(", ")}
                </Text>
              </View>
              <Text
                style={{
                  color: theme.textMuted,
                  fontFamily: theme.body,
                  fontSize: 13,
                  lineHeight: 18,
                }}
              >
                {entry.label}
                {entry.detector ? ` · ${entry.detector}` : ""}
              </Text>
              {entry.reason ? (
                <Text
                  style={{
                    color: theme.textDim,
                    fontFamily: theme.body,
                    fontSize: 14,
                    lineHeight: 21,
                  }}
                >
                  {entry.reason}
                </Text>
              ) : null}
            </View>
          ))}
        </Section>
      ) : null}
    </Screen>
  );
}
