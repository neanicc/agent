import React, { useEffect, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { statusColor, statusLabel, theme } from "../theme";
import {
  ActionButton,
  EmptyState,
  LabeledInput,
  PageHeader,
  Screen,
  Section,
  StatusBadge,
} from "../components/ui";
import { LoopGuardClient, Project, RunSummary } from "../client";
import { AppIcon } from "../components/AppIcon";

export function DashboardScreen({
  client,
  onStarted,
  onOpenRun,
}: {
  client: LoopGuardClient;
  onStarted: (runId: string, mode: "flag" | "auto") => void;
  onOpenRun: (runId: string, mode: "flag" | "auto") => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [running, setRunning] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [mode, setMode] = useState<"flag" | "auto">("flag");
  const [task, setTask] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    client.projects().then(setProjects).catch((e) => setError(String(e.message || e)));
    const tick = () =>
      client
        .runs()
        .then((rs) => setRunning(rs.filter((r) => ["running", "awaiting_decision"].includes(r.status))))
        .catch(() => {});
    tick();
    const id = setInterval(tick, 3000);
    return () => clearInterval(id);
  }, [client]);

  async function launch() {
    if (!selected) return;
    setLaunching(true);
    setError(null);
    try {
      const runId = await client.startRun({
        projectId: selected.id,
        mode,
        task: selected.customizable && task.trim() ? task.trim() : null,
      });
      onStarted(runId, mode);
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setLaunching(false);
    }
  }

  return (
    <Screen>
      <PageHeader
        subtitle="Choose a project and how LoopGuard should respond when the run gets stuck."
        title="Run an agent"
      />

      {running.length > 0 ? (
        <Section
          accessory={<StatusBadge color={theme.warn} label={`${running.length} active`} />}
          title="Active runs"
        >
          {running.map((r) => (
            <Pressable
              accessibilityRole="button"
              key={r.id}
              onPress={() => onOpenRun(r.id, r.mode as "flag" | "auto")}
              style={({ pressed }) => ({
                alignItems: "center",
                backgroundColor: pressed ? theme.surfaceHigh : theme.surfaceInset,
                borderColor:
                  r.status === "awaiting_decision" ? theme.warn : theme.border,
                borderRadius: theme.radius,
                borderWidth: 1,
                flexDirection: "row",
                gap: theme.space(3),
                minHeight: 58,
                padding: theme.space(3),
              })}
            >
              <AppIcon
                color={statusColor(r.status)}
                fallback={r.status === "awaiting_decision" ? "alert-triangle" : "activity"}
                size={18}
                symbol={
                  r.status === "awaiting_decision"
                    ? "exclamationmark.triangle.fill"
                    : "waveform.path.ecg"
                }
              />
              <View style={{ flex: 1, gap: theme.space(1), minWidth: 0 }}>
                <Text
                  numberOfLines={1}
                  style={{
                    color: theme.text,
                    fontFamily: theme.bodySemibold,
                    fontSize: 15,
                    lineHeight: 19,
                  }}
                >
                  {r.label}
                </Text>
                <Text
                  style={{
                    color: theme.textMuted,
                    fontFamily: theme.body,
                    fontSize: 12,
                    lineHeight: 16,
                  }}
                >
                  {r.mode === "auto" ? "Automatic repair" : "Review required on loop"}
                </Text>
              </View>
              <StatusBadge color={statusColor(r.status)} label={statusLabel(r.status)} />
            </Pressable>
          ))}
        </Section>
      ) : null}

      {projects.length > 0 ? (
        <Section flush title="Projects">
          {projects.map((project, index) => {
            const isSelected = selected?.id === project.id;
            return (
              <View
                key={project.id}
                style={{
                  borderTopColor: index === 0 ? theme.surface : theme.border,
                  borderTopWidth: index === 0 ? 0 : 1,
                  paddingHorizontal: theme.space(4),
                  paddingVertical: theme.space(3),
                }}
              >
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ expanded: isSelected, selected: isSelected }}
                  onPress={() => setSelected(isSelected ? null : project)}
                  style={({ pressed }) => ({
                    alignItems: "center",
                    flexDirection: "row",
                    gap: theme.space(3),
                    minHeight: 58,
                    opacity: pressed ? 0.7 : 1,
                  })}
                >
                  <View
                    style={{
                      alignItems: "center",
                      backgroundColor: isSelected ? theme.surfaceHigh : theme.surfaceInset,
                      borderColor: isSelected ? theme.accent : theme.border,
                      borderRadius: theme.radius,
                      borderWidth: 1,
                      height: 42,
                      justifyContent: "center",
                      width: 42,
                    }}
                  >
                    <AppIcon
                      color={isSelected ? theme.accent : theme.textDim}
                      fallback={project.kind === "multi" ? "layers" : "play"}
                      size={18}
                      symbol={project.kind === "multi" ? "rectangle.stack" : "play.fill"}
                    />
                  </View>
                  <View style={{ flex: 1, gap: theme.space(1), minWidth: 0 }}>
                    <Text
                      numberOfLines={1}
                      style={{
                        color: theme.text,
                        fontFamily: theme.display,
                        fontSize: 17,
                        lineHeight: 21,
                      }}
                    >
                      {project.label}
                    </Text>
                    <Text
                      numberOfLines={2}
                      style={{
                        color: theme.textDim,
                        fontFamily: theme.body,
                        fontSize: 13,
                        lineHeight: 18,
                      }}
                    >
                      {project.blurb}
                    </Text>
                  </View>
                  <View style={{ alignItems: "flex-end", gap: theme.space(2) }}>
                    <Text
                      style={{
                        color: theme.textMuted,
                        fontFamily: theme.bodyMedium,
                        fontSize: 12,
                      }}
                    >
                      {project.agents.length} {project.agents.length === 1 ? "agent" : "agents"}
                    </Text>
                    <AppIcon
                      color={theme.textMuted}
                      fallback={isSelected ? "chevron-up" : "chevron-down"}
                      size={16}
                      symbol={isSelected ? "chevron.up" : "chevron.down"}
                    />
                  </View>
                </Pressable>
                {isSelected ? (
                  <View
                    style={{
                      borderTopColor: theme.border,
                      borderTopWidth: 1,
                      gap: theme.space(3),
                      marginTop: theme.space(3),
                      paddingTop: theme.space(4),
                    }}
                  >
                    <Text
                      style={{
                        color: theme.textDim,
                        fontFamily: theme.bodyMedium,
                        fontSize: 13,
                        lineHeight: 17,
                      }}
                    >
                      Intervention mode
                    </Text>
                    <View style={{ flexDirection: "row", gap: theme.space(2) }}>
                      <ActionButton
                        label="Review"
                        onPress={() => setMode("flag")}
                        style={{ flex: 1 }}
                        variant={mode === "flag" ? "selected" : "secondary"}
                      />
                      <ActionButton
                        label="Auto-fix"
                        onPress={() => setMode("auto")}
                        style={{ flex: 1 }}
                        variant={mode === "auto" ? "selected" : "secondary"}
                      />
                    </View>
                    <Text
                      style={{
                        color: theme.textMuted,
                        fontFamily: theme.body,
                        fontSize: 13,
                        lineHeight: 18,
                      }}
                    >
                      {mode === "flag"
                        ? "Pause when LoopGuard needs a decision."
                        : "Apply the judge’s correction automatically."}
                    </Text>
                    {project.customizable ? (
                      <LabeledInput
                        helper={project.hint || "Describe what the agent should accomplish."}
                        label="Agent task"
                        onChangeText={setTask}
                        placeholder="Describe the task"
                        value={task}
                      />
                    ) : null}
                    <ActionButton
                      label="Launch agent"
                      loading={launching}
                      onPress={launch}
                    />
                  </View>
                ) : null}
              </View>
            );
          })}
        </Section>
      ) : null}

      {!projects.length && !error ? (
        <EmptyState
          body="Projects will appear when the LoopGuard service responds."
          fallback="server"
          symbol="server.rack"
          title="Loading projects"
        />
      ) : null}

      {error ? (
        <View
          accessibilityLiveRegion="polite"
          style={{
            alignItems: "flex-start",
            flexDirection: "row",
            gap: theme.space(2),
            paddingVertical: theme.space(2),
          }}
        >
          <AppIcon
            color={theme.danger}
            fallback="alert-circle"
            size={18}
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
            {error}
          </Text>
        </View>
      ) : null}
    </Screen>
  );
}
