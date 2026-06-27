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
      paddingHorizontal: theme.space(4),
      borderRadius: theme.radius,
      flexGrow: 1,
      alignItems: "center",
    }}
  >
    <Text style={{ color: "#0A0A0A", fontWeight: "700", fontSize: 14 }}>{label}</Text>
  </Pressable>
);

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
          <Text style={{ color: theme.textDim }}>Judge: </Text>
          {pending.judge_reasoning}
        </Text>
      ) : null}
      {pending.suggested_message ? (
        <Text style={{ color: theme.text, fontSize: 13, lineHeight: 19 }}>
          <Text style={{ color: theme.textDim }}>Suggested fix: </Text>
          {pending.suggested_message}
        </Text>
      ) : null}
      <View style={{ flexDirection: "row", gap: theme.space(2) }}>
        {pending.suggested_message ? (
          <Btn label="Approve fix" color={theme.ok} onPress={() => onAction("approve")} />
        ) : null}
        <Btn label="Ignore once" color={theme.textDim} onPress={() => onAction("continue_once")} />
        <Btn label="Terminate" color={theme.danger} onPress={() => onAction("terminate")} />
      </View>
      <TextInput
        value={custom}
        onChangeText={setCustom}
        placeholder="Or send a custom correction…"
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
    </View>
  );
}
