import React, { useState } from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";
import { PendingDecision } from "../runReducer";
import { AppIcon } from "./AppIcon";
import { ActionButton, LabeledInput } from "./ui";

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
  const submitCustom = () => {
    const message = custom.trim();
    if (message) onAction("inject", message);
  };

  return (
    <View
      style={{
        backgroundColor: theme.surfaceHigh,
        borderRadius: theme.radius,
        borderColor: theme.borderStrong,
        borderWidth: 1,
        gap: theme.space(4),
        padding: theme.space(4),
      }}
    >
      <View style={{ alignItems: "center", flexDirection: "row", gap: theme.space(2) }}>
        <AppIcon
          color={theme.warn}
          fallback="alert-triangle"
          size={20}
          symbol="exclamationmark.triangle.fill"
        />
        <View style={{ flex: 1, gap: theme.space(1) }}>
          <Text
            style={{
              color: theme.text,
              fontFamily: theme.display,
              fontSize: 18,
              lineHeight: 22,
            }}
          >
            Loop needs review
          </Text>
          <Text
            style={{
              color: theme.warn,
              fontFamily: theme.monoMedium,
              fontSize: 12,
              lineHeight: 16,
            }}
          >
            {pending.detector || "detector"}
            {pending.similarity != null ? ` · ${pending.similarity.toFixed(2)}` : ""}
          </Text>
        </View>
      </View>
      {pending.judge_reasoning ? (
        <Text
          style={{
            color: theme.textDim,
            fontFamily: theme.body,
            fontSize: 14,
            lineHeight: 21,
          }}
        >
          <Text style={{ color: theme.text, fontFamily: theme.bodySemibold }}>Judge: </Text>
          {pending.judge_reasoning}
          {pending.judge_confidence != null ? (
            <Text style={{ color: theme.textMuted, fontFamily: theme.mono }}>
              {` · ${pending.judge_confidence.toFixed(2)}`}
            </Text>
          ) : null}
        </Text>
      ) : null}
      {pending.suggested_message ? (
        <View
          style={{
            backgroundColor: theme.surfaceInset,
            borderColor: theme.border,
            borderRadius: theme.radius,
            borderWidth: 1,
            gap: theme.space(1),
            padding: theme.space(3),
          }}
        >
          <Text style={{ color: theme.textMuted, fontFamily: theme.bodyMedium, fontSize: 12 }}>
            Suggested correction
          </Text>
          <Text
            style={{
              color: theme.text,
              fontFamily: theme.body,
              fontSize: 14,
              lineHeight: 20,
            }}
          >
            {pending.suggested_message}
          </Text>
        </View>
      ) : null}
      <View style={{ gap: theme.space(2) }}>
        {pending.suggested_message ? (
          <ActionButton
            label="Apply suggested correction"
            onPress={() => onAction("approve")}
          />
        ) : null}
        <View style={{ flexDirection: "row", gap: theme.space(2) }}>
          <ActionButton
            label="Continue once"
            onPress={() => onAction("continue_once")}
            style={{ flex: 1 }}
            variant="secondary"
          />
          <ActionButton
            label="Allow tool"
            onPress={() => onAction("allowlist")}
            style={{ flex: 1 }}
            variant="secondary"
          />
        </View>
        <ActionButton
          label="Terminate run"
          onPress={() => onAction("terminate")}
          variant="danger"
        />
      </View>
      <LabeledInput
        helper="Send a different instruction to the agent."
        label="Custom correction"
        multiline
        onChangeText={setCustom}
        onSubmitEditing={submitCustom}
        placeholder="Describe the correction"
        returnKeyType="send"
        value={custom}
      />
      <ActionButton
        disabled={!custom.trim()}
        label="Send custom correction"
        onPress={submitCustom}
        variant="quiet"
      />
    </View>
  );
}
