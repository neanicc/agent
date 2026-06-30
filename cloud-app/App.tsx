import React, { useEffect, useState } from "react";
import { View } from "react-native";
import { useFonts } from "expo-font";
import { StatusBar } from "expo-status-bar";
import { SpaceGrotesk_600SemiBold } from "@expo-google-fonts/space-grotesk/600SemiBold";
import { SpaceGrotesk_700Bold } from "@expo-google-fonts/space-grotesk/700Bold";
import { IBMPlexSans_400Regular } from "@expo-google-fonts/ibm-plex-sans/400Regular";
import { IBMPlexSans_500Medium } from "@expo-google-fonts/ibm-plex-sans/500Medium";
import { IBMPlexSans_600SemiBold } from "@expo-google-fonts/ibm-plex-sans/600SemiBold";
import { IBMPlexMono_400Regular } from "@expo-google-fonts/ibm-plex-mono/400Regular";
import { IBMPlexMono_500Medium } from "@expo-google-fonts/ibm-plex-mono/500Medium";
import { IBMPlexMono_600SemiBold } from "@expo-google-fonts/ibm-plex-mono/600SemiBold";
import { LoopGuardClient } from "./src/client";
import { loadServerUrl } from "./src/storage";
import { theme } from "./src/theme";
import { TabBar, TabKey } from "./src/components/TabBar";
import { ConnectScreen } from "./src/screens/ConnectScreen";
import { DashboardScreen } from "./src/screens/DashboardScreen";
import { MissionControlScreen } from "./src/screens/MissionControlScreen";
import { HistoryScreen } from "./src/screens/HistoryScreen";
import { AutoFixScreen } from "./src/screens/AutoFixScreen";
import { AllowlistScreen } from "./src/screens/AllowlistScreen";

export default function App() {
  const [fontsLoaded] = useFonts({
    SpaceGrotesk_600SemiBold,
    SpaceGrotesk_700Bold,
    IBMPlexSans_400Regular,
    IBMPlexSans_500Medium,
    IBMPlexSans_600SemiBold,
    IBMPlexMono_400Regular,
    IBMPlexMono_500Medium,
    IBMPlexMono_600SemiBold,
  });
  const [client, setClient] = useState<LoopGuardClient | null>(null);
  const [booted, setBooted] = useState(false);
  const [tab, setTab] = useState<TabKey>("run");
  const [activeRun, setActiveRun] = useState<{ runId: string; mode: "flag" | "auto" } | null>(null);

  // On launch, reconnect to the last server automatically if it's reachable.
  useEffect(() => {
    (async () => {
      const url = await loadServerUrl();
      if (url) {
        const c = new LoopGuardClient(url);
        if (await c.health()) setClient(c);
      }
      setBooted(true);
    })();
  }, []);

  if (!booted || !fontsLoaded) return <View style={{ flex: 1, backgroundColor: theme.bg }} />;

  if (!client) {
    return (
      <>
        <StatusBar style="light" />
        <ConnectScreen onConnected={(url) => setClient(new LoopGuardClient(url))} />
      </>
    );
  }

  let body: React.ReactNode;
  if (tab === "run") {
    body = activeRun ? (
      <MissionControlScreen client={client} runId={activeRun.runId} onExit={() => setActiveRun(null)} />
    ) : (
      <DashboardScreen
        client={client}
        onStarted={(runId, mode) => setActiveRun({ runId, mode })}
        onOpenRun={(runId, mode) => setActiveRun({ runId, mode })}
      />
    );
  } else if (tab === "history") {
    body = <HistoryScreen client={client} onLaunch={() => setTab("run")} />;
  } else if (tab === "autofix") {
    body = <AutoFixScreen client={client} onLaunch={() => setTab("run")} />;
  } else {
    body = <AllowlistScreen client={client} onLaunch={() => setTab("run")} />;
  }

  return (
    <View style={{ flex: 1, backgroundColor: theme.bg }}>
      <StatusBar style="light" />
      <View style={{ flex: 1 }}>{body}</View>
      <TabBar
        active={tab}
        onChange={(t) => {
          setTab(t);
        }}
      />
    </View>
  );
}
