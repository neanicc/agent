import React, { useEffect, useState } from "react";
import { ActivityIndicator, Text, TextInput, View } from "react-native";
import { theme } from "../theme";
import { LoopGuardClient } from "../client";
import { loadServerUrl, saveServerUrl } from "../storage";
import { Card, CTAButton, Hero, Pill, Screen } from "../components/ui";

export function ConnectScreen({ onConnected }: { onConnected: (url: string) => void }) {
  const [url, setUrl] = useState("http://localhost:8000");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { loadServerUrl().then((saved) => { if (saved) setUrl(saved); }); }, []);

  async function test() {
    setBusy(true); setStatus("Checking…");
    const ok = await new LoopGuardClient(url).health();
    setBusy(false); setStatus(ok ? "ok" : "unreachable");
    if (ok) { await saveServerUrl(url); onConnected(url); }
  }

  return (
    <Screen scroll={false}>
      <View style={{ flex: 1, justifyContent: "center", gap: theme.space(5) }}>
        <View style={{ width: 76, height: 76, borderRadius: 24, backgroundColor: "rgba(56,189,248,0.14)", borderWidth: 1, borderColor: "rgba(56,189,248,0.36)", alignItems: "center", justifyContent: "center" }}>
          <Text style={{ fontSize: 34 }}>🛡️</Text>
        </View>
        <Hero eyebrow="LoopGuard cloud" title="Mission control for resilient agents." subtitle="Connect to your LoopGuard server, launch runs, catch loops, and approve fixes from a polished native command deck." />
        <Card>
          <View style={{ flexDirection: "row", gap: theme.space(2), flexWrap: "wrap" }}>
            <Pill label="live runs" /><Pill label="auto-fix" color={theme.purple} /><Pill label="cost guard" color={theme.ok} />
          </View>
          <TextInput value={url} onChangeText={setUrl} autoCapitalize="none" autoCorrect={false} placeholder="http://localhost:8000" placeholderTextColor={theme.textMuted} style={{ color: theme.text, borderWidth: 1, borderColor: theme.borderStrong, backgroundColor: "rgba(2,6,23,0.35)", borderRadius: theme.radius, padding: theme.space(4), fontSize: 15, fontFamily: theme.mono }} />
          <CTAButton label={busy ? "Connecting…" : "Connect to server"} onPress={test} disabled={busy} />
          {busy ? <ActivityIndicator color={theme.accent} /> : null}
          {status && status !== "ok" && !busy ? <Text style={{ color: status === "unreachable" ? theme.danger : theme.textDim, lineHeight: 20 }}>{status === "unreachable" ? "Couldn't reach the server. Check the URL and that `loopguard serve` is running." : status}</Text> : null}
        </Card>
      </View>
    </Screen>
  );
}
