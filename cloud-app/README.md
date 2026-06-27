# LoopGuard Cloud (mobile app)

A React Native (Expo) "mission control" app for monitoring real LoopGuard-guarded agents and
intervening from your phone. It talks to the LoopGuard server (`loopguard serve`) over the
network — for the demo the server runs on your laptop; in production you deploy the same server
and point the app at its URL.

## Sections

- **Run** — pick a demo project (or the custom one and type any task), choose **Flag** or
  **Auto** mode, and launch a real agent. Shows agents currently running.
- **Mission Control** (opens on launch) — live tool calls, a tokens/$ meter (agent **and** judge
  cost), inline auto-fix badges, and on a flagged loop a decision card with the four genuine
  actions: **Approve fix · Continue once · Allow tool · Terminate**, plus a free-text correction.
- **Agents** — every run, live and past: status, agents, cost, what it concluded, with a detail
  view of events and auto-fixes.
- **Auto-fixes** — the feed of loops the guard caught and resolved on its own (auto mode).
- **Allowlist** — tools you told the guard to stop flagging, and why.

## Prerequisites

- Node.js 18+ and npm
- The LoopGuard server running:
  ```bash
  cd ../loopguard
  pip install -e ".[server,cerebras]"      # or [server,litellm]
  echo "CEREBRAS_API_KEY=..." > .env        # the model the agent + judge use
  loopguard serve --port 8000
  ```

## Run the app

```bash
cd cloud-app
npm install
npx expo start
```

Then:
- **Browser:** press `w` (the server sends permissive CORS headers, so web works).
- **iOS simulator:** press `i` (requires Xcode).
- **Physical phone:** install **Expo Go** and scan the QR code.

## Connect

On the first screen, enter the server URL (it's remembered for next launch):
- Simulator / web on the same machine: `http://localhost:8000`
- Physical phone: your laptop's LAN IP, e.g. `http://192.168.1.42:8000`
  (`ipconfig getifaddr en0` on macOS). The phone and laptop must be on the same network.

## Tests

```bash
npm test          # run-reducer unit tests (jest-expo)
npx tsc --noEmit  # typecheck
npx expo export --platform web   # prove the bundle builds
```

> The reducer logic, client types, and web bundle are checked automatically; the native visual
> layer is verified by running the app (simulator / Expo Go / web).

## Notes & limitations

- **Reconnect:** the app remembers the server URL and reconnects on launch. Opening a run (from
  Run or Agents) connects to its WebSocket, and the server replays the run's events + any pending
  decision — so you can rejoin a run already in flight. There is no automatic mid-session socket
  reconnect yet; if the socket drops, re-open the run.
- **Server state is in memory only** (no persistence); restarting `loopguard serve` clears past
  runs, the auto-fix feed, and the allowlist.
- The server has **CORS open and no auth** — fine for a local/LAN demo, not a public deployment.
