import React, { useEffect, useState } from "react";
import { Text, View } from "react-native";
import { theme } from "../theme";
import { LoopGuardClient } from "../client";
import { loadServerUrl, saveServerUrl } from "../storage";
import { ActionButton, LabeledInput, PageHeader, Screen, Section } from "../components/ui";

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

  const error =
    status === "unreachable"
      ? "The server did not respond. Check the URL and confirm that loopguard serve is running."
      : null;

  return (
    <Screen contentStyle={{ justifyContent: "center" }} scroll={false}>
      <View style={{ gap: theme.space(6) }}>
        <View style={{ alignItems: "center", flexDirection: "row", gap: theme.space(3) }}>
          <View
            style={{
              alignItems: "center",
              backgroundColor: theme.surfaceHigh,
              borderColor: theme.borderStrong,
              borderRadius: theme.radius,
              borderWidth: 1,
              height: 48,
              justifyContent: "center",
              width: 48,
            }}
          >
            <Text
              style={{
                color: theme.accent,
                fontFamily: theme.displayBold,
                fontSize: 16,
                letterSpacing: -0.4,
              }}
            >
              LG
            </Text>
          </View>
          <View>
            <Text
              style={{
                color: theme.text,
                fontFamily: theme.display,
                fontSize: 18,
                lineHeight: 22,
              }}
            >
              LoopGuard
            </Text>
            <Text
              style={{
                color: theme.textMuted,
                fontFamily: theme.body,
                fontSize: 13,
                lineHeight: 17,
              }}
            >
              Agent operations
            </Text>
          </View>
        </View>
        <PageHeader
          subtitle="Point the app at the LoopGuard service that owns your runs."
          title="Connect to your server"
        />
        <Section>
          <LabeledInput
            autoCapitalize="none"
            autoCorrect={false}
            error={error}
            helper="Simulator: localhost. Physical phone: use your computer’s LAN address."
            keyboardType="url"
            label="Server URL"
            loading={busy}
            onChangeText={(value) => {
              setUrl(value);
              if (status === "unreachable") setStatus(null);
            }}
            onSubmitEditing={test}
            placeholder="http://localhost:8000"
            returnKeyType="go"
            style={{ fontFamily: theme.mono }}
            value={url}
          />
          <ActionButton
            disabled={!url.trim()}
            label="Connect to server"
            loading={busy}
            onPress={test}
            state={error ? "error" : "default"}
          />
        </Section>
      </View>
    </Screen>
  );
}
