import React, { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, Text, TextInput, View } from "react-native";
import { theme } from "../theme";
import { LoopGuardClient, Project, RunSummary } from "../client";

function Badge({ text, color }: { text: string; color: string }) {
  return (
    <Text
      style={{
        color,
        borderColor: color,
        borderWidth: 1,
        borderRadius: 8,
        paddingHorizontal: 6,
        paddingVertical: 1,
        fontSize: 11,
        fontWeight: "700",
        overflow: "hidden",
      }}
    >
      {text}
    </Text>
  );
}

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
    <ScrollView
      style={{ flex: 1, backgroundColor: theme.bg }}
      contentContainerStyle={{ padding: theme.space(5), paddingTop: theme.space(14), gap: theme.space(3) }}
    >
      <Text style={{ color: theme.text, fontSize: 24, fontWeight: "800" }}>Launch an agent</Text>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>
        Each project runs a real Cerebras agent against a real repo. They’re primed to get stuck —
        LoopGuard catches the loop.
      </Text>

      {running.length > 0 ? (
        <View style={{ gap: theme.space(2), marginTop: theme.space(2) }}>
          <Text style={{ color: theme.textDim, fontSize: 12, fontWeight: "700" }}>RUNNING NOW</Text>
          {running.map((r) => (
            <Pressable
              key={r.id}
              onPress={() => onOpenRun(r.id, r.mode as "flag" | "auto")}
              style={{
                flexDirection: "row",
                justifyContent: "space-between",
                backgroundColor: theme.surfaceHigh,
                borderWidth: 1,
                borderColor: r.status === "awaiting_decision" ? theme.warn : theme.border,
                borderRadius: theme.radius,
                padding: theme.space(3),
              }}
            >
              <Text style={{ color: theme.text, fontSize: 13 }}>{r.label}</Text>
              <Text style={{ color: r.status === "awaiting_decision" ? theme.warn : theme.accent, fontSize: 12 }}>
                {r.status === "awaiting_decision" ? "needs you" : r.status}
              </Text>
            </Pressable>
          ))}
        </View>
      ) : null}

      {projects.length === 0 && !error ? <ActivityIndicator color={theme.accent} /> : null}

      {projects.map((p) => {
        const on = selected?.id === p.id;
        return (
          <Pressable
            key={p.id}
            onPress={() => setSelected(p)}
            style={{
              backgroundColor: theme.surface,
              borderWidth: 1,
              borderColor: on ? theme.accent : theme.border,
              borderRadius: theme.radius,
              padding: theme.space(4),
              gap: theme.space(2),
            }}
          >
            <View style={{ flexDirection: "row", alignItems: "center", gap: theme.space(2) }}>
              <Text style={{ color: theme.text, fontWeight: "700", fontSize: 15, flex: 1 }}>{p.label}</Text>
              <Badge text={p.kind === "multi" ? `${p.agents.length} agents` : "1 agent"}
                     color={p.kind === "multi" ? theme.purple : theme.textDim} />
            </View>
            <Text style={{ color: theme.textDim, fontSize: 13, lineHeight: 18 }}>{p.blurb}</Text>
            {on ? (
              <View style={{ gap: theme.space(3), marginTop: theme.space(2) }}>
                <View style={{ flexDirection: "row", gap: theme.space(2) }}>
                  {(["flag", "auto"] as const).map((m) => (
                    <Pressable
                      key={m}
                      onPress={() => setMode(m)}
                      style={{
                        flex: 1,
                        borderWidth: 1,
                        borderColor: mode === m ? theme.accent : theme.border,
                        backgroundColor: mode === m ? theme.surfaceHigh : "transparent",
                        borderRadius: theme.radius,
                        padding: theme.space(3),
                      }}
                    >
                      <Text style={{ color: theme.text, fontWeight: "700", fontSize: 13 }}>
                        {m === "flag" ? "Flag mode" : "Auto mode"}
                      </Text>
                      <Text style={{ color: theme.textDim, fontSize: 11 }}>
                        {m === "flag" ? "Pause & ask me" : "Auto-apply the fix"}
                      </Text>
                    </Pressable>
                  ))}
                </View>
                {p.customizable ? (
                  <View style={{ gap: theme.space(1) }}>
                    <TextInput
                      value={task}
                      onChangeText={setTask}
                      placeholder="Type any task for the agent…"
                      placeholderTextColor={theme.textDim}
                      style={{
                        color: theme.text,
                        borderWidth: 1,
                        borderColor: theme.border,
                        borderRadius: theme.radius,
                        padding: theme.space(3),
                        fontSize: 13,
                      }}
                    />
                    {p.hint ? <Text style={{ color: theme.textDim, fontSize: 11 }}>{p.hint}</Text> : null}
                  </View>
                ) : null}
                <Pressable
                  onPress={launch}
                  disabled={launching}
                  style={{
                    backgroundColor: theme.accent,
                    padding: theme.space(4),
                    borderRadius: theme.radius,
                    alignItems: "center",
                    opacity: launching ? 0.6 : 1,
                  }}
                >
                  {launching ? <ActivityIndicator color="#fff" /> : (
                    <Text style={{ color: "#fff", fontWeight: "700" }}>Launch agent</Text>
                  )}
                </Pressable>
              </View>
            ) : null}
          </Pressable>
        );
      })}

      {error ? <Text style={{ color: theme.danger, fontSize: 13 }}>{error}</Text> : null}
    </ScrollView>
  );
}
