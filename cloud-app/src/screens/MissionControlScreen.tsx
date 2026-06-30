import React, { useEffect, useReducer, useRef } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, Text, View } from "react-native";
import { theme, statusColor, statusLabel } from "../theme";
import { LoopGuardClient } from "../client";
import { initialRunState, runReducer } from "../runReducer";
import { EventRow } from "../components/EventRow";
import { DecisionCard } from "../components/DecisionCard";
import { Meter } from "../components/Meter";
import { AppIcon } from "../components/AppIcon";
import { ActionButton, EmptyState, Screen, StatusBadge } from "../components/ui";

export function MissionControlScreen({
  client,
  runId,
  onExit,
}: {
  client: LoopGuardClient;
  runId: string;
  onExit: () => void;
}) {
  const [state, dispatch] = useReducer(runReducer, undefined, initialRunState);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    // Connect to the existing run; the server replays events + any pending decision,
    // so this works for a fresh run AND for reconnecting to one already in flight.
    const ws = client.openSocket(runId, dispatch, (e) =>
      dispatch({ type: "error", data: { message: e } }),
    );
    wsRef.current = ws;
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  const onAction = (action: string, message?: string) => {
    if (wsRef.current) LoopGuardClient.intervene(wsRef.current, action, message);
  };

  const done = ["completed", "stopped", "error"].includes(state.status);

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={{ flex: 1 }}
    >
      <Screen contentStyle={{ gap: theme.space(3), paddingBottom: theme.space(4) }} scroll={false}>
        <View
          style={{
            alignItems: "center",
            flexDirection: "row",
            gap: theme.space(3),
            minHeight: theme.hitTarget,
          }}
        >
          <Pressable
            accessibilityLabel="Back to run list"
            accessibilityRole="button"
            onPress={onExit}
            style={({ pressed }) => ({
              alignItems: "center",
              flexDirection: "row",
              gap: theme.space(1),
              minHeight: theme.hitTarget,
              opacity: pressed ? 0.6 : 1,
            })}
          >
            <AppIcon
              color={theme.accent}
              fallback="chevron-left"
              size={17}
              symbol="chevron.left"
            />
            <Text style={{ color: theme.accent, fontFamily: theme.bodySemibold, fontSize: 14 }}>
              Runs
            </Text>
          </Pressable>
          <Text
            numberOfLines={1}
            style={{
              color: theme.text,
              flex: 1,
              fontFamily: theme.display,
              fontSize: 18,
              lineHeight: 23,
            }}
          >
            Mission Control
          </Text>
          <StatusBadge color={statusColor(state.status)} label={statusLabel(state.status)} />
        </View>

        <Meter tokens={state.totalTokens} cost={state.totalCost} judgeCost={state.judgeCost} />

        {state.allowlist.length > 0 ? (
          <View style={{ alignItems: "center", flexDirection: "row", gap: theme.space(2) }}>
            <AppIcon
              color={theme.ok}
              fallback="check-circle"
              size={15}
              symbol="checkmark.shield"
            />
            <Text
              numberOfLines={1}
              style={{
                color: theme.textDim,
                flex: 1,
                fontFamily: theme.body,
                fontSize: 13,
                lineHeight: 18,
              }}
            >
              Allowed for this run: {state.allowlist.join(", ")}
            </Text>
          </View>
        ) : null}

        <View
          style={{
            backgroundColor: theme.surface,
            borderColor: theme.border,
            borderRadius: theme.radiusLg,
            borderWidth: 1,
            flex: 1,
            minHeight: 140,
            overflow: "hidden",
          }}
        >
          <View
            style={{
              alignItems: "center",
              borderBottomColor: theme.border,
              borderBottomWidth: 1,
              flexDirection: "row",
              justifyContent: "space-between",
              minHeight: theme.hitTarget,
              paddingHorizontal: theme.space(3),
            }}
          >
            <Text
              style={{
                color: theme.text,
                fontFamily: theme.bodySemibold,
                fontSize: 14,
              }}
            >
              Live events
            </Text>
            <Text
              style={{
                color: theme.textMuted,
                fontFamily: theme.monoMedium,
                fontSize: 12,
                fontVariant: ["tabular-nums"],
              }}
            >
              {state.events.length}
            </Text>
          </View>
          <ScrollView contentContainerStyle={{ flexGrow: 1 }}>
            {state.events.length === 0 ? (
              <View style={{ padding: theme.space(4) }}>
                <EmptyState
                  body="Tool calls will appear here as the agent works."
                  fallback="activity"
                  symbol="waveform.path.ecg"
                  title="Waiting for the first event"
                />
              </View>
            ) : null}
            {state.events.map((event, index) => (
              <EventRow event={event} index={index} key={`${event.step ?? index}-${index}`} />
            ))}

            {state.autoFixes.map((fix, index) => (
              <View
                key={`auto-fix-${index}`}
                style={{
                  alignItems: "flex-start",
                  backgroundColor: theme.surfaceHigh,
                  borderBottomColor: theme.border,
                  borderBottomWidth: 1,
                  flexDirection: "row",
                  gap: theme.space(2),
                  padding: theme.space(3),
                }}
              >
                <AppIcon
                  color={fix.terminated ? theme.danger : theme.ok}
                  fallback={fix.terminated ? "x-circle" : "tool"}
                  size={17}
                  symbol={fix.terminated ? "xmark.circle.fill" : "wrench.and.screwdriver.fill"}
                />
                <View style={{ flex: 1, gap: theme.space(1) }}>
                  <Text
                    style={{
                      color: fix.terminated ? theme.danger : theme.ok,
                      fontFamily: theme.bodySemibold,
                      fontSize: 13,
                    }}
                  >
                    {fix.terminated ? "Run stopped automatically" : "Correction applied"}
                  </Text>
                  {fix.applied_fix ? (
                    <Text
                      style={{
                        color: theme.textDim,
                        fontFamily: theme.body,
                        fontSize: 13,
                        lineHeight: 18,
                      }}
                    >
                      {fix.applied_fix}
                    </Text>
                  ) : null}
                </View>
              </View>
            ))}

            {state.finalText ? (
              <View style={{ gap: theme.space(2), padding: theme.space(4) }}>
                <View style={{ alignItems: "center", flexDirection: "row", gap: theme.space(2) }}>
                  <AppIcon
                    color={theme.ok}
                    fallback="check-circle"
                    size={17}
                    symbol="checkmark.circle.fill"
                  />
                  <Text
                    style={{
                      color: theme.text,
                      fontFamily: theme.bodySemibold,
                      fontSize: 14,
                    }}
                  >
                    Agent result
                  </Text>
                </View>
                <Text
                  style={{
                    color: theme.textDim,
                    fontFamily: theme.body,
                    fontSize: 14,
                    lineHeight: 21,
                  }}
                >
                  {state.finalText}
                </Text>
              </View>
            ) : null}
            {state.error ? (
              <View
                accessibilityLiveRegion="polite"
                style={{
                  alignItems: "flex-start",
                  flexDirection: "row",
                  gap: theme.space(2),
                  padding: theme.space(4),
                }}
              >
                <AppIcon
                  color={theme.danger}
                  fallback="alert-circle"
                  size={17}
                  symbol="exclamationmark.circle.fill"
                />
                <Text
                  style={{
                    color: theme.danger,
                    flex: 1,
                    fontFamily: theme.body,
                    fontSize: 14,
                    lineHeight: 20,
                  }}
                >
                  {state.error}
                </Text>
              </View>
            ) : null}
          </ScrollView>
        </View>

        {state.pending ? <DecisionCard pending={state.pending} onAction={onAction} /> : null}

        {done ? (
          <ActionButton label="Return to run list" onPress={onExit} variant="secondary" />
        ) : null}
      </Screen>
    </KeyboardAvoidingView>
  );
}
