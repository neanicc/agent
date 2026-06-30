import React, { useEffect, useState } from "react";
import { Pressable, RefreshControl, Text, View } from "react-native";
import { statusColor, statusLabel, theme } from "../theme";
import {
  ActionButton,
  EmptyState,
  PageHeader,
  Screen,
  Section,
  StatusBadge,
} from "../components/ui";
import { LoopGuardClient, RunSummary } from "../client";
import { AppIcon } from "../components/AppIcon";
import { EventRow } from "../components/EventRow";
import { RunEvent } from "../runReducer";

type RunDetail = {
  label: string;
  task?: string;
  final_text?: string;
  auto_actions?: Array<{ step?: number; applied_fix?: string }>;
  events?: RunEvent[];
};

function Row({
  run,
  onPress,
  isLast,
}: {
  run: RunSummary;
  onPress: () => void;
  isLast: boolean;
}) {
  const cost = run.summary?.cost_usd ?? 0;
  const judge = run.summary?.judge_cost_usd ?? 0;
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => ({
        backgroundColor: pressed ? theme.surfaceHigh : theme.surface,
        borderBottomColor: theme.border,
        borderBottomWidth: isLast ? 0 : 1,
        gap: theme.space(2),
        minHeight: 88,
        padding: theme.space(4),
      })}
    >
      <View style={{ alignItems: "center", flexDirection: "row", gap: theme.space(2) }}>
        <AppIcon
          color={statusColor(run.status)}
          fallback={run.status === "error" ? "alert-circle" : "activity"}
          size={17}
          symbol={run.status === "error" ? "exclamationmark.circle.fill" : "waveform.path.ecg"}
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
          {run.label}
        </Text>
        <StatusBadge color={statusColor(run.status)} label={statusLabel(run.status)} />
      </View>
      <Text
        numberOfLines={1}
        style={{
          color: theme.textDim,
          fontFamily: theme.body,
          fontSize: 13,
          lineHeight: 18,
        }}
      >
        {run.agents.join(" + ")} · {run.mode === "auto" ? "Auto-fix" : "Review"} · {run.events} events
      </Text>
      <View style={{ alignItems: "center", flexDirection: "row", flexWrap: "wrap", gap: theme.space(3) }}>
        <Text
          style={{
            color: theme.ok,
            fontFamily: theme.bodySemibold,
            fontSize: 12,
            fontVariant: ["tabular-nums"],
          }}
        >
          ${(cost + judge).toFixed(4)}
        </Text>
        {run.auto_fixes > 0 ? (
          <Text style={{ color: theme.textMuted, fontFamily: theme.body, fontSize: 12 }}>
            {run.auto_fixes} {run.auto_fixes === 1 ? "correction" : "corrections"}
          </Text>
        ) : null}
        {run.allowlist.length > 0 ? (
          <Text numberOfLines={1} style={{ color: theme.warn, fontFamily: theme.body, fontSize: 12 }}>
            Allowed: {run.allowlist.join(", ")}
          </Text>
        ) : null}
      </View>
    </Pressable>
  );
}

function Detail({
  client,
  runId,
  onBack,
}: {
  client: LoopGuardClient;
  runId: string;
  onBack: () => void;
}) {
  const [run, setRun] = useState<RunDetail | null>(null);
  useEffect(() => {
    client.run(runId).then(setRun).catch(() => {});
  }, [client, runId]);

  return (
    <Screen>
      <Pressable
        accessibilityRole="button"
        onPress={onBack}
        style={({ pressed }) => ({
          alignItems: "center",
          alignSelf: "flex-start",
          flexDirection: "row",
          gap: theme.space(1),
          minHeight: theme.hitTarget,
          opacity: pressed ? 0.6 : 1,
        })}
      >
        <AppIcon color={theme.accent} fallback="chevron-left" size={17} symbol="chevron.left" />
        <Text style={{ color: theme.accent, fontFamily: theme.bodySemibold, fontSize: 14 }}>
          All agents
        </Text>
      </Pressable>
      {!run ? (
        <Section>
          <EmptyState
            body="The run record is being loaded from the LoopGuard service."
            fallback="activity"
            symbol="waveform.path.ecg"
            title="Loading run"
          />
        </Section>
      ) : (
        <>
          <PageHeader subtitle={run.task ? `Task: ${run.task}` : undefined} title={run.label} />
          {run.final_text ? (
            <Section title="Result">
              <View style={{ alignItems: "flex-start", flexDirection: "row", gap: theme.space(2) }}>
                <AppIcon
                  color={theme.ok}
                  fallback="check-circle"
                  size={18}
                  symbol="checkmark.circle.fill"
                />
                <Text
                  style={{
                    color: theme.textDim,
                    flex: 1,
                    fontFamily: theme.body,
                    fontSize: 15,
                    lineHeight: 23,
                  }}
                >
                  {run.final_text}
                </Text>
              </View>
            </Section>
          ) : null}
          {run.auto_actions?.length ? (
            <Section title="Corrections">
              {run.auto_actions.map((action, index) => (
                <View
                  key={`${action.step ?? index}-${index}`}
                  style={{
                    borderTopColor: index === 0 ? theme.surface : theme.border,
                    borderTopWidth: index === 0 ? 0 : 1,
                    flexDirection: "row",
                    gap: theme.space(2),
                    paddingVertical: theme.space(2),
                  }}
                >
                  <AppIcon
                    color={theme.ok}
                    fallback="tool"
                    size={17}
                    symbol="wrench.and.screwdriver"
                  />
                  <View style={{ flex: 1, gap: theme.space(1) }}>
                    <Text
                      style={{
                        color: theme.textMuted,
                        fontFamily: theme.monoMedium,
                        fontSize: 12,
                      }}
                    >
                      Step {action.step ?? "—"}
                    </Text>
                    {action.applied_fix ? (
                      <Text
                        style={{
                          color: theme.text,
                          fontFamily: theme.body,
                          fontSize: 14,
                          lineHeight: 20,
                        }}
                      >
                        {action.applied_fix}
                      </Text>
                    ) : null}
                  </View>
                </View>
              ))}
            </Section>
          ) : null}
          <Section flush title="Events">
            {run.events?.length ? (
              run.events.map((event, index) => (
                <EventRow event={event} index={index} key={`${event.step ?? index}-${index}`} />
              ))
            ) : (
              <View style={{ padding: theme.space(4) }}>
                <EmptyState
                  body="This run has not recorded a tool event."
                  fallback="activity"
                  symbol="waveform.path.ecg"
                  title="No events"
                />
              </View>
            )}
          </Section>
        </>
      )}
    </Screen>
  );
}

export function HistoryScreen({
  client,
  onLaunch,
}: {
  client: LoopGuardClient;
  onLaunch: () => void;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = () => client.runs().then(setRuns).catch(() => {});
  useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [client]);

  if (open) return <Detail client={client} runId={open} onBack={() => setOpen(null)} />;

  return (
    <Screen
      refreshControl={
        <RefreshControl
          onRefresh={() => {
            setRefreshing(true);
            load().finally(() => setRefreshing(false));
          }}
          refreshing={refreshing}
          tintColor={theme.accent}
        />
      }
    >
      <PageHeader
        subtitle="Live and past runs, including interventions, cost, and final output."
        title="Agents"
      />
      {runs.length === 0 ? (
        <Section>
          <EmptyState
            action={
              <ActionButton label="Open Run" onPress={onLaunch} variant="secondary" />
            }
            body="Launch an agent to start an operational history."
            fallback="clock"
            symbol="clock.arrow.circlepath"
            title="No runs recorded"
          />
        </Section>
      ) : (
        <Section flush title="Run history">
          {runs.map((run, index) => (
            <Row
              isLast={index === runs.length - 1}
              key={run.id}
              onPress={() => setOpen(run.id)}
              run={run}
            />
          ))}
        </Section>
      )}
    </Screen>
  );
}
