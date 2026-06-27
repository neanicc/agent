# LoopGuard Cloud (mobile app)

A React Native (Expo) "mission control" app that monitors a real LoopGuard-guarded agent
live and lets you intervene from your phone — terminate it, approve the judge's suggested
fix, send a custom correction, or ignore a flagged loop once.

It talks to the LoopGuard server (`loopguard serve`) over the network. For this demo the
server runs on your laptop; in production you'd deploy the same server elsewhere and point
the app at that URL instead — the only change is the URL.

## Prerequisites

- Node.js 18+ and npm
- The LoopGuard server running:
  ```bash
  cd ../loopguard
  pip install -e ".[server,cerebras]"      # or [server,litellm]
  export CEREBRAS_API_KEY=...              # the model the agent + judge use
  loopguard serve --port 8000
  ```

## Run the app

```bash
cd cloud-app
npm install
npx expo start
```

Then:
- **iOS simulator:** press `i` (requires Xcode).
- **Physical phone:** install **Expo Go** and scan the QR code.
- **Browser (quickest):** press `w` (Expo web).

## Connect

On the first screen, enter the server URL:
- Simulator / web on the same machine: `http://localhost:8000`
- Physical phone: your laptop's LAN IP, e.g. `http://192.168.1.42:8000` (run `ipconfig getifaddr en0` on macOS). The phone and laptop must be on the same network.

## Using it

1. **Start** a run in **flag** (pause and ask you) or **auto** (auto-apply the judge's fix) mode.
2. **Mission Control** streams the agent's real tool calls and a live tokens/$ meter.
3. When LoopGuard detects a loop (flag mode), a **decision card** shows the judge's reasoning
   and suggested fix. Choose **Approve fix**, **Ignore once**, **Terminate**, or type a custom
   correction. (In auto mode the fix is applied automatically.)

## Tests

```bash
npm test          # pure run-reducer unit tests (jest-expo)
npx tsc --noEmit  # typecheck
npx expo export --platform web   # prove the bundle builds
```

> Note: the native mobile UI is best verified by running it (simulator / Expo Go). The reducer
> logic, types, and web bundle are checked automatically; the visual layer is verified by hand.

## Known limitations

- **No automatic WebSocket reconnect yet.** If the socket drops mid-run, the app shows an error
  rather than reconnecting. The run keeps going server-side and the server supports resync — re-open
  the app and it can fetch `GET /runs/{id}` (events + any pending decision are replayed on connect).
  Auto-reconnect is a straightforward follow-up.
- The server keeps run state in memory only (no persistence); restarting it clears past runs.
