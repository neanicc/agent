import React, { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, Text, TextInput, View } from "react-native";
import { theme } from "../theme";
import { Card, CTAButton, Hero, Pill, Screen } from "../components/ui";
import { LoopGuardClient, Project, RunSummary } from "../client";


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
      <Hero
        eyebrow="Launch deck"
        title="Launch an agent"
        subtitle="Choose a scenario, pick human-in-the-loop or autonomous repair, then watch LoopGuard keep the run on rails."
      />

      {running.length > 0 ? (
        <Card style={{ borderColor: "rgba(251,191,36,0.32)" }}>
          <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
            <Text style={{ color: theme.warn, fontSize: 12, fontWeight: "900", letterSpacing: 1.2 }}>RUNNING NOW</Text>
            <Pill label={`${running.length} active`} color={theme.warn} />
          </View>
          {running.map((r) => (
            <Pressable key={r.id} onPress={() => onOpenRun(r.id, r.mode as "flag" | "auto")} style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", backgroundColor: "rgba(2,6,23,0.30)", borderWidth: 1, borderColor: r.status === "awaiting_decision" ? theme.warn : theme.border, borderRadius: theme.radius, padding: theme.space(3) }}>
              <Text style={{ color: theme.text, fontSize: 13, fontWeight: "700", flex: 1 }}>{r.label}</Text>
              <Pill label={r.status === "awaiting_decision" ? "needs you" : r.status} color={r.status === "awaiting_decision" ? theme.warn : theme.accent} />
            </Pressable>
          ))}
        </Card>
      ) : null}

      {projects.length === 0 && !error ? <ActivityIndicator color={theme.accent} /> : null}

      {projects.map((p) => {
        const on = selected?.id === p.id;
        return (
          <Pressable key={p.id} onPress={() => setSelected(p)}>
            <Card style={{ borderColor: on ? "rgba(56,189,248,0.66)" : theme.border, backgroundColor: on ? "rgba(23,32,51,0.94)" : theme.surfaceGlass }}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: theme.space(3) }}>
                <View style={{ width: 46, height: 46, borderRadius: 16, backgroundColor: p.kind === "multi" ? "rgba(192,132,252,0.16)" : "rgba(56,189,248,0.16)", alignItems: "center", justifyContent: "center" }}>
                  <Text style={{ fontSize: 22 }}>{p.kind === "multi" ? "✦" : "▶"}</Text>
                </View>
                <Text style={{ color: theme.text, fontWeight: "900", fontSize: 17, flex: 1 }}>{p.label}</Text>
                <Pill label={p.kind === "multi" ? `${p.agents.length} agents` : "1 agent"} color={p.kind === "multi" ? theme.purple : theme.textDim} />
              </View>
              <Text style={{ color: theme.textDim, fontSize: 13, lineHeight: 20 }}>{p.blurb}</Text>
              {on ? (
                <View style={{ gap: theme.space(3), marginTop: theme.space(1) }}>
                  <View style={{ flexDirection: "row", gap: theme.space(2) }}>
                    {(["flag", "auto"] as const).map((m) => (
                      <Pressable key={m} onPress={() => setMode(m)} style={{ flex: 1, borderWidth: 1, borderColor: mode === m ? theme.accent : theme.border, backgroundColor: mode === m ? "rgba(56,189,248,0.12)" : "rgba(2,6,23,0.24)", borderRadius: theme.radius, padding: theme.space(3) }}>
                        <Text style={{ color: theme.text, fontWeight: "900", fontSize: 13 }}>{m === "flag" ? "Flag mode" : "Auto mode"}</Text>
                        <Text style={{ color: theme.textDim, fontSize: 11, marginTop: 2 }}>{m === "flag" ? "Pause & ask me" : "Auto-apply the fix"}</Text>
                      </Pressable>
                    ))}
                  </View>
                  {p.customizable ? (
                    <View style={{ gap: theme.space(1) }}>
                      <TextInput value={task} onChangeText={setTask} placeholder="Type any task for the agent…" placeholderTextColor={theme.textMuted} style={{ color: theme.text, borderWidth: 1, borderColor: theme.borderStrong, backgroundColor: "rgba(2,6,23,0.24)", borderRadius: theme.radius, padding: theme.space(3), fontSize: 13 }} />
                      {p.hint ? <Text style={{ color: theme.textDim, fontSize: 11 }}>{p.hint}</Text> : null}
                    </View>
                  ) : null}
                  <CTAButton label={launching ? "Launching…" : "Launch agent"} onPress={launch} disabled={launching} />
                </View>
              ) : null}
            </Card>
          </Pressable>
        );
      })}

      {error ? <Text style={{ color: theme.danger, fontSize: 13 }}>{error}</Text> : null}
    </Screen>
  );
}
