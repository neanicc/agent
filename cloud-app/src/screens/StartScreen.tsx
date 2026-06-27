import React, { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { theme } from "../theme";

export function StartScreen({ onStart }: { onStart: (mode: "flag" | "auto") => void }) {
  const [mode, setMode] = useState<"flag" | "auto">("flag");

  const Opt = ({ m, label, desc }: { m: "flag" | "auto"; label: string; desc: string }) => (
    <Pressable
      onPress={() => setMode(m)}
      style={{
        borderWidth: 1,
        borderColor: mode === m ? theme.accent : theme.border,
        backgroundColor: mode === m ? theme.surfaceHigh : theme.surface,
        borderRadius: theme.radius,
        padding: theme.space(4),
        gap: theme.space(1),
      }}
    >
      <Text style={{ color: theme.text, fontWeight: "700", fontSize: 16 }}>{label}</Text>
      <Text style={{ color: theme.textDim, fontSize: 13 }}>{desc}</Text>
    </Pressable>
  );

  return (
    <View
      style={{
        flex: 1,
        backgroundColor: theme.bg,
        padding: theme.space(6),
        gap: theme.space(4),
        justifyContent: "center",
      }}
    >
      <Text style={{ color: theme.text, fontSize: 22, fontWeight: "800" }}>New run</Text>
      <Opt m="flag" label="Flag mode" desc="Pause on a loop and ask me what to do." />
      <Opt m="auto" label="Auto mode" desc="Auto-apply the judge's fix and keep going." />
      <Pressable
        onPress={() => onStart(mode)}
        style={{
          backgroundColor: theme.accent,
          padding: theme.space(4),
          borderRadius: theme.radius,
          alignItems: "center",
          marginTop: theme.space(2),
        }}
      >
        <Text style={{ color: "#fff", fontWeight: "700" }}>Start run</Text>
      </Pressable>
    </View>
  );
}
