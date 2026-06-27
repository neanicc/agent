import React, { useState } from "react";
import { StatusBar } from "expo-status-bar";
import { LoopGuardClient } from "./src/client";
import { ConnectScreen } from "./src/screens/ConnectScreen";
import { StartScreen } from "./src/screens/StartScreen";
import { MissionControlScreen } from "./src/screens/MissionControlScreen";

export default function App() {
  const [client, setClient] = useState<LoopGuardClient | null>(null);
  const [mode, setMode] = useState<"flag" | "auto" | null>(null);

  return (
    <>
      <StatusBar style="light" />
      {!client ? (
        <ConnectScreen onConnected={(url) => setClient(new LoopGuardClient(url))} />
      ) : !mode ? (
        <StartScreen onStart={setMode} />
      ) : (
        <MissionControlScreen client={client} mode={mode} />
      )}
    </>
  );
}
