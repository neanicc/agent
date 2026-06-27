import React, { useState } from "react";
import { Pressable, Text, TextInput, View } from "react-native";
import { theme } from "../theme";
import { PendingDecision } from "../runReducer";

const Btn = ({ label, color, onPress }: { label: string; color: string; onPress: () => void }) => (
  <Pressable
    onPress={onPress}
    style={{
      backgroundColor: color,
      paddingVertical: theme.space(3),
      paddingHorizontal: theme.space(3),
      borderRadius: theme.radius,
      flexGrow: 1,
      flexBasis: "45%",
      alignItems: "center",
    }}
  >
    <Text style={{ color: "#0A0A0A", fontWeight: "700", fontSize: 13 }}>{label}</Text>
  </Pressable>
);

// The four genuine LoopGuard actions, matching the terminal [t/c/a/i]:
//   Terminate (t) · Continue once (c) · Allow tool (a) · Prompt / Approve fix (i)
export function DecisionCard({
  pending,
  onAction,
}: {
  pending: PendingDecision;
  onAction: (action: string, message?: string) => void;
}) {
  const [custom, setCustom] = useState("");
  return (
    <View
      style={{
        backgroundColor: theme.surfaceHigh,
        borderWidth: 1,
        borderColor: theme.warn,
        borderRadius: theme.radius,
        padding: theme.space(4),
        gap: theme.space(3),
      }}
    >
      <Text style={{ color: theme.warn, fontWeight: "700", fontSize: 15 }}>
        ⚠ Loop detected · {pending.detector}
        {pending.similarity ? ` (${pending.similarity.toFixed(2)})` : ""}
      </Text>
      {pending.judge_reasoning ? (
        <Text style={{ color: theme.text, fontSize: 13, lineHeight: 19 }}>
          <Text style={{ color: theme.purple, fontWeight: "700" }}>Judge: </Text>
          {pending.judge_reasoning}
          {pending.judge_confidence != null ? (
            <Text style={{ color: theme.textDim }}>{`  (${pending.judge_confidence.toFixed(2)})`}</Text>
          ) : null}
        </Text>
      ) : null}
      {pending.suggested_message ? (
        <Text style={{ color: theme.text, fontSize: 13, lineHeight: 19 }}>
          <Text style={{ color: theme.textDim }}>Suggested fix: </Text>
          {pending.suggested_message}
        </Text>
      ) : null}
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) }}>
        {pending.suggested_message ? (
          <Btn label="Approve fix" color={theme.ok} onPress={() => onAction("approve")} />
        ) : null}
        <Btn label="Continue once" color={theme.accent} onPress={() => onAction("continue_once")} />
        <Btn label="Allow tool" color={theme.purple} onPress={() => onAction("allowlist")} />
        <Btn label="Terminate" color={theme.danger} onPress={() => onAction("terminate")} />
      </View>
      <TextInput
        value={custom}
        onChangeText={setCustom}
        placeholder="Or type a correction prompt and submit…"
        placeholderTextColor={theme.textDim}
        style={{
          color: theme.text,
          borderWidth: 1,
          borderColor: theme.border,
          borderRadius: theme.radius,
          padding: theme.space(3),
          fontSize: 13,
        }}
        onSubmitEditing={() => custom.trim() && onAction("inject", custom.trim())}
      />
      <Text style={{ color: theme.textDim, fontSize: 11 }}>
        Approve = inject the judge’s fix · Continue = allow one more step · Allow = stop flagging this
        tool (added to allowlist) · Terminate = stop the agent
      </Text>
    </View>
  );
}
