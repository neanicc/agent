import React, { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, Text, TextInput, View } from "react-native";
import { theme } from "../theme";
import { LoopGuardClient } from "../client";
import { loadServerUrl, saveServerUrl } from "../storage";

export function ConnectScreen({ onConnected }: { onConnected: (url: string) => void }) {
  const [url, setUrl] = useState("http://localhost:8000");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadServerUrl().then((saved) => {
      if (saved) setUrl(saved);
    });
  }, []);

  async function test() {
    setBusy(true);
    setStatus("Checking…");
    const ok = await new LoopGuardClient(url).health();
    setBusy(false);
    setStatus(ok ? "ok" : "unreachable");
    if (ok) {
      await saveServerUrl(url);
      onConnected(url);
    }
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
      <Text style={{ color: theme.text, fontSize: 30, fontWeight: "800" }}>LoopGuard</Text>
      <Text style={{ color: theme.textDim, fontSize: 14, marginBottom: theme.space(2) }}>
        Mission control for your agents. Connect to your LoopGuard server.
      </Text>
      <TextInput
        value={url}
        onChangeText={setUrl}
        autoCapitalize="none"
        autoCorrect={false}
        placeholder="http://localhost:8000"
        placeholderTextColor={theme.textDim}
        style={{
          color: theme.text,
          borderWidth: 1,
          borderColor: theme.border,
          backgroundColor: theme.surface,
          borderRadius: theme.radius,
          padding: theme.space(4),
          fontSize: 15,
          fontFamily: theme.mono,
        }}
      />
      <Pressable
        onPress={test}
        disabled={busy}
        style={{
          backgroundColor: theme.accent,
          padding: theme.space(4),
          borderRadius: theme.radius,
          alignItems: "center",
          opacity: busy ? 0.6 : 1,
        }}
      >
        {busy ? <ActivityIndicator color="#fff" /> : (
          <Text style={{ color: "#fff", fontWeight: "700" }}>Connect</Text>
        )}
      </Pressable>
      {status && status !== "ok" && !busy ? (
        <Text style={{ color: status === "unreachable" ? theme.danger : theme.textDim }}>
          {status === "unreachable"
            ? "Couldn't reach the server. Check the URL and that `loopguard serve` is running."
            : status}
        </Text>
      ) : null}
    </View>
  );
}
