import React, { useEffect, useReducer, useRef } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { theme, statusColor } from "../theme";
import { LoopGuardClient } from "../client";
import { initialRunState, runReducer } from "../runReducer";
import { EventRow } from "../components/EventRow";
import { DecisionCard } from "../components/DecisionCard";
import { Meter } from "../components/Meter";

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
    <View style={{ flex: 1, backgroundColor: theme.bg, padding: theme.space(5), paddingTop: theme.space(14) }}>
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: theme.space(3) }}>
        <Pressable onPress={onExit}>
          <Text style={{ color: theme.accent, fontSize: 14 }}>‹ Back</Text>
        </Pressable>
        <Text style={{ color: theme.text, fontSize: 16, fontWeight: "800" }}>Mission Control</Text>
        <Text style={{ color: statusColor(state.status), fontSize: 12, fontWeight: "700" }}>
          {state.status}
        </Text>
      </View>

      <Meter tokens={state.totalTokens} cost={state.totalCost} judgeCost={state.judgeCost} />

      {state.allowlist.length > 0 ? (
        <Text style={{ color: theme.purple, fontSize: 12, marginTop: theme.space(2) }}>
          Allowlisted: {state.allowlist.join(", ")}
        </Text>
      ) : null}

      <ScrollView style={{ flex: 1, marginTop: theme.space(3) }}>
        {state.events.map((e, i) => (
          <EventRow key={i} event={e} index={i} />
        ))}

        {state.autoFixes.map((f, i) => (
          <View
            key={`af-${i}`}
            style={{
              borderLeftWidth: 3,
              borderLeftColor: f.terminated ? theme.danger : theme.purple,
              paddingLeft: theme.space(3),
              paddingVertical: theme.space(2),
              marginVertical: theme.space(1),
            }}
          >
            <Text style={{ color: f.terminated ? theme.danger : theme.purple, fontSize: 12, fontWeight: "700" }}>
              {f.terminated ? "Auto-terminated (no fix)" : "Auto-fix applied"}
            </Text>
            {f.applied_fix ? (
              <Text style={{ color: theme.text, fontSize: 12, marginTop: 2 }}>{f.applied_fix}</Text>
            ) : null}
          </View>
        ))}

        {state.finalText ? (
          <View style={{ marginTop: theme.space(4), backgroundColor: theme.surface, borderRadius: theme.radius, padding: theme.space(4) }}>
            <Text style={{ color: theme.textDim, fontSize: 11, marginBottom: 2 }}>AGENT RESULT</Text>
            <Text style={{ color: theme.ok, fontSize: 14 }}>{state.finalText}</Text>
          </View>
        ) : null}
        {state.error ? (
          <Text style={{ color: theme.danger, marginTop: theme.space(4) }}>{state.error}</Text>
        ) : null}
      </ScrollView>

      {state.pending ? (
        <View style={{ marginTop: theme.space(3) }}>
          <DecisionCard pending={state.pending} onAction={onAction} />
        </View>
      ) : null}

      {done ? (
        <Pressable
          onPress={onExit}
          style={{ marginTop: theme.space(3), backgroundColor: theme.surfaceHigh, padding: theme.space(4), borderRadius: theme.radius, alignItems: "center" }}
        >
          <Text style={{ color: theme.text, fontWeight: "700" }}>Done — back to launch</Text>
        </Pressable>
      ) : null}
    </View>
  );
}
