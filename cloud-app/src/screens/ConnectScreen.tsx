import React, { useState } from "react";
import { Pressable, Text, TextInput, View } from "react-native";
import { theme } from "../theme";
import { LoopGuardClient } from "../client";

export function ConnectScreen({ onConnected }: { onConnected: (url: string) => void }) {
  const [url, setUrl] = useState("http://localhost:8000");
  const [status, setStatus] = useState<string | null>(null);

  async function test() {
    setStatus("Checking…");
    const ok = await new LoopGuardClient(url).health();
    setStatus(ok ? "ok" : "unreachable");
    if (ok) onConnected(url);
  }

  return (
    <View
      style={{
        flex: 1,
        backgroundColor: theme.bg,
        padding: theme.space(6),
        justifyContent: "center",
        gap: theme.space(4),
      }}
    >
      <Text style={{ color: theme.text, fontSize: 28, fontWeight: "800" }}>LoopGuard</Text>
      <Text style={{ color: theme.textDim, fontSize: 14 }}>Connect to your LoopGuard server.</Text>
      <TextInput
        value={url}
        onChangeText={setUrl}
        autoCapitalize="none"
        autoCorrect={false}
        style={{
          color: theme.text,
          borderWidth: 1,
          borderColor: theme.border,
          borderRadius: theme.radius,
          padding: theme.space(4),
          fontSize: 15,
        }}
      />
      <Pressable
        onPress={test}
        style={{
          backgroundColor: theme.accent,
          padding: theme.space(4),
          borderRadius: theme.radius,
          alignItems: "center",
        }}
      >
        <Text style={{ color: "#fff", fontWeight: "700" }}>Connect</Text>
      </Pressable>
      {status && status !== "ok" ? (
        <Text style={{ color: status === "unreachable" ? theme.danger : theme.textDim }}>
          {status}
        </Text>
      ) : null}
    </View>
  );
}
