import React, { useEffect, useReducer, useRef } from "react";
import { ScrollView, Text, View } from "react-native";
import { theme } from "../theme";
import { LoopGuardClient } from "../client";
import { initialRunState, runReducer } from "../runReducer";
import { EventRow } from "../components/EventRow";
import { DecisionCard } from "../components/DecisionCard";
import { Meter } from "../components/Meter";

export function MissionControlScreen({
  client,
  mode,
}: {
  client: LoopGuardClient;
  mode: "flag" | "auto";
}) {
  const [state, dispatch] = useReducer(runReducer, undefined, initialRunState);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    (async () => {
      try {
        const runId = await client.startRun({ mode, model: "cerebras/gpt-oss-120b" });
        ws = client.openSocket(runId, dispatch, (e) =>
          dispatch({ type: "error", data: { message: e } }),
        );
        wsRef.current = ws;
      } catch (e: any) {
        dispatch({ type: "error", data: { message: String(e?.message || e) } });
      }
    })();
    return () => ws?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onAction = (action: string, message?: string) => {
    if (wsRef.current) LoopGuardClient.intervene(wsRef.current, action, message);
  };

  return (
    <View
      style={{
        flex: 1,
        backgroundColor: theme.bg,
        padding: theme.space(5),
        paddingTop: theme.space(14),
      }}
    >
      <View
        style={{
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: theme.space(3),
        }}
      >
        <Text style={{ color: theme.text, fontSize: 18, fontWeight: "800" }}>Mission Control</Text>
        <Text style={{ color: theme.textDim, fontSize: 12 }}>{state.status}</Text>
      </View>
      <Meter tokens={state.totalTokens} cost={state.totalCost} />
      <ScrollView style={{ flex: 1, marginTop: theme.space(3) }}>
        {state.events.map((e, i) => (
          <EventRow key={i} event={e} index={i} />
        ))}
        {state.finalText ? (
          <Text style={{ color: theme.ok, marginTop: theme.space(4), fontSize: 14 }}>
            Agent: {state.finalText}
          </Text>
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
    </View>
  );
}
