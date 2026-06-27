import React, { useEffect, useState } from "react";
import { Pressable, RefreshControl, ScrollView, Text, View } from "react-native";
import { theme, statusColor } from "../theme";
import { LoopGuardClient, RunSummary } from "../client";

function Row({ r, onPress }: { r: RunSummary; onPress: () => void }) {
  const cost = r.summary?.cost_usd ?? 0;
  const judge = r.summary?.judge_cost_usd ?? 0;
  return (
    <Pressable
      onPress={onPress}
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
        <Text style={{ color: theme.text, fontWeight: "700", fontSize: 14, flex: 1 }}>{r.label}</Text>
        <Text style={{ color: statusColor(r.status), fontSize: 12, fontWeight: "700" }}>{r.status}</Text>
      </View>
      <Text style={{ color: theme.textDim, fontSize: 12 }}>
        {r.agents.join(" + ")} · {r.mode} · {r.events} events
      </Text>
      <View style={{ flexDirection: "row", gap: theme.space(3), marginTop: 2 }}>
        <Text style={{ color: theme.ok, fontSize: 12, fontFamily: theme.mono }}>
          ${(cost + judge).toFixed(4)}
        </Text>
        {r.auto_fixes > 0 ? (
          <Text style={{ color: theme.purple, fontSize: 12 }}>{r.auto_fixes} auto-fix</Text>
        ) : null}
        {r.allowlist.length > 0 ? (
          <Text style={{ color: theme.warn, fontSize: 12 }}>allow: {r.allowlist.join(",")}</Text>
        ) : null}
      </View>
    </Pressable>
  );
}

function Detail({ client, runId, onBack }: { client: LoopGuardClient; runId: string; onBack: () => void }) {
  const [run, setRun] = useState<any | null>(null);
  useEffect(() => {
    client.run(runId).then(setRun).catch(() => {});
  }, [client, runId]);
  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: theme.bg }}
      contentContainerStyle={{ padding: theme.space(5), paddingTop: theme.space(14), gap: theme.space(3) }}
    >
      <Pressable onPress={onBack}>
        <Text style={{ color: theme.accent, fontSize: 14 }}>‹ All agents</Text>
      </Pressable>
      {!run ? <Text style={{ color: theme.textDim }}>Loading…</Text> : (
        <>
          <Text style={{ color: theme.text, fontSize: 20, fontWeight: "800" }}>{run.label}</Text>
          <Text style={{ color: theme.textDim, fontSize: 13 }}>Task: {run.task}</Text>
          {run.final_text ? (
            <View style={{ backgroundColor: theme.surface, borderRadius: theme.radius, padding: theme.space(4) }}>
              <Text style={{ color: theme.textDim, fontSize: 11 }}>RESULT</Text>
              <Text style={{ color: theme.ok, fontSize: 13 }}>{run.final_text}</Text>
            </View>
          ) : null}
          {(run.auto_actions || []).map((a: any, i: number) => (
            <View key={i} style={{ borderLeftWidth: 3, borderLeftColor: theme.purple, paddingLeft: theme.space(3) }}>
              <Text style={{ color: theme.purple, fontSize: 12, fontWeight: "700" }}>Auto-fix @ step {a.step}</Text>
              {a.applied_fix ? <Text style={{ color: theme.text, fontSize: 12 }}>{a.applied_fix}</Text> : null}
            </View>
          ))}
          <Text style={{ color: theme.textDim, fontSize: 12, marginTop: theme.space(2) }}>EVENTS</Text>
          {(run.events || []).map((e: any, i: number) => (
            <Text key={i} style={{ color: e.is_error ? theme.danger : theme.text, fontSize: 12, fontFamily: theme.mono }}>
              {e.step}. {e.agent} {e.tool}({e.args?.path ?? ""}) {e.tripped ? "⚠" : ""}
            </Text>
          ))}
        </>
      )}
    </ScrollView>
  );
}

export function HistoryScreen({ client }: { client: LoopGuardClient }) {
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
    <ScrollView
      style={{ flex: 1, backgroundColor: theme.bg }}
      contentContainerStyle={{ padding: theme.space(5), paddingTop: theme.space(14), gap: theme.space(3) }}
      refreshControl={
        <RefreshControl refreshing={refreshing} tintColor={theme.accent}
          onRefresh={() => { setRefreshing(true); load().finally(() => setRefreshing(false)); }} />
      }
    >
      <Text style={{ color: theme.text, fontSize: 24, fontWeight: "800" }}>Agents</Text>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>Every run, live and past — what worked, what got stuck, what it cost.</Text>
      {runs.length === 0 ? (
        <Text style={{ color: theme.textDim, fontSize: 13, marginTop: theme.space(2) }}>
          No runs yet. Launch one from the Run tab.
        </Text>
      ) : null}
      {runs.map((r) => (
        <Row key={r.id} r={r} onPress={() => setOpen(r.id)} />
      ))}
    </ScrollView>
  );
}
