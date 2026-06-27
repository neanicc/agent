# LoopGuard Cloud — Mobile Monitoring & Intervention App

**Date:** 2026-06-27
**Status:** Approved design, pre-implementation
**Goal:** A React Native (Expo) "mission control" app that monitors a real LoopGuard-guarded
agent in real time and lets a human intervene (terminate / approve the judge's fix / send a
custom prompt / ignore once) from their phone — backed by a real local server wrapping the
existing LoopGuard engine.

---

## 1. Background & framing

We already have a real two-layer LoopGuard engine and a real live agent (the Cerebras
repo-inspector that genuinely loops hunting `package.json`). This project adds a **cloud-style
front-end**: a phone app talking over the network to a server that runs the real engine.

A React Native app is JavaScript and cannot run the Python engine in-process, so the engine
runs as a **local FastAPI server** for the demo. This is "cloud" in every way that matters
except the host: in production the same server is deployed elsewhere and the app points at that
URL instead of the laptop's LAN address. For the demo we run it over the local network — a very
close scope to real cloud.

**This must be robust enough to survive a judge grilling it**, so the backend is built and
tested like real software, not a prop.

## 2. Architecture (three parts)

### Part A — Engine hooks (small additions to `loopguard` core)
Two reusable extension points on `LoopGuard`, so a non-terminal front-end can observe and
control runs. The core stays UI-free (consistent with the existing design).

- `LoopGuard(config, judge=None, on_observe=None, on_pause=None)`:
  - `on_observe: Callable[[LoopEvent, LoopDecision], None] | None` — fired after **every**
    `observe()` with the event and the final decision. The server streams from this.
  - `on_pause: Callable[[LoopDecision], LoopDecision] | None` — when `action == "pause"`,
    `_handle` calls `(self.on_pause or pause_for_action)(decision)`. The server supplies a
    handler that blocks until the app responds, instead of reading the terminal.
- `observe()` is refactored to compute the decision once and fire `on_observe` on every return
  path (allowlisted, no-trip, suppressed, handled) before returning.

These are the only core changes. Terminal behavior is unchanged when the hooks are absent.

### Part B — Backend (`loopguard.server`, new `loopguard[server]` extra)
FastAPI + uvicorn. Wraps the real engine and the existing live agent (`run_agent` +
`live_demo` building blocks). New package `src/loopguard/server/`:
- `schemas.py` — pydantic request/response + WebSocket message models.
- `runs.py` — `RunState`, in-memory `RunRegistry`, and the run orchestration (background
  thread per run, pause/resume handoff via a `threading.Event` + response slot).
- `app.py` — FastAPI app, REST routes, WebSocket endpoint, broadcasting.
- A `loopguard serve` CLI command launches uvicorn.

**Run lifecycle:** a run executes `run_agent` in a background thread (real network calls).
- App "flag" mode → guard `action="pause"` + server `on_pause` → the run **blocks** on a loop
  trip awaiting the app's decision.
- App "auto" mode → guard `action="auto"` → autonomous; the auto-applied decision is streamed
  via `on_observe` (no blocking).
- `on_observe` appends every event/decision to `RunState` and broadcasts to WebSocket clients.
- Pause timeout (default 300s) → the run terminates safely (`developer_action="terminate"`).

**Endpoints:**
- `GET /health` → `{ "status": "ok", "version": "..." }`
- `POST /runs` body `{ mode: "flag"|"auto", model?: str, provider?: str }` → `{ run_id }`
  (starts the run; 400 if no API key / provider error, with a clear message).
- `GET /runs` → list of run summaries.
- `GET /runs/{id}` → full `RunState` (events so far, status, pending decision) — used by the
  app to resync after a reconnect.
- `POST /runs/{id}/intervene` body `{ action, message? }` → REST fallback for interventions.
- `WS /runs/{id}/ws` → live stream (server→client) + interventions (client→server).

**WebSocket protocol** — server→client messages:
- `{ type: "event", data: { step, tool, args, output, is_error, tokens, cost_usd, total_cost, total_tokens } }`
- `{ type: "decision_required", data: { detector, similarity, reason, judge_reasoning, judge_confidence, suggested_message } }`
- `{ type: "status", data: { status, summary } }`
- `{ type: "done", data: { summary, final_text, stopped_by_guard } }`
- `{ type: "error", data: { message } }`

client→server message:
- `{ type: "intervene", action: "terminate"|"approve"|"inject"|"continue_once", message?: str }`
  - `approve` → inject the pending decision's judge `suggested_message` and continue.
  - `inject` → inject the user-typed `message` and continue.
  - `continue_once` → allow once, no correction.
  - `terminate` → stop the run.

The `on_pause` handler maps these onto existing `LoopDecision` fields
(`developer_action`, `allowed`, `suggested_message`) that `run_agent` already honors
(stop when `not allowed`; inject when `suggested_message` is set).

### Part C — App (`cloud-app/`, Expo + TypeScript)
A single-stack app (no heavy router; a small screen state machine) using the **native
WebSocket** (no extra networking deps) and `AsyncStorage` for the saved server URL.

**Screens:**
1. **Connect/Settings** — server URL field (default `http://localhost:8000`; on a phone, the
   laptop's LAN IP), a "Test connection" hitting `/health`, persisted via AsyncStorage.
2. **Start** — choose mode (**flag** / **auto**), model (default `cerebras/gpt-oss-120b`),
   and a **Start run** button.
3. **Mission Control** (the core screen) —
   - Header: status pill (running / paused / done / error) + a live **tokens · $cost** meter.
   - Live **event feed**: each tool call as a row (tool, path, ok/error), monospace, newest
     pinned, auto-scroll.
   - **Decision card** (bottom sheet) on `decision_required`: detector + similarity, the
     judge's reasoning + confidence, the suggested fix, and buttons
     **Terminate / Approve fix / Custom prompt / Ignore once**. Auto mode shows an
     "✅ auto-fixed" inline badge instead of the card.
   - Completion summary (events, tokens, total cost incl. judge) when `done`.

**Design:** clean, Vercel-style, dark. Real typography (Geist or Inter via
`@expo-google-fonts`, monospace for code/tool rows), restrained single accent color, generous
spacing, subtle motion on new events and the decision card. No emoji-soup, no default-system
slop, no rainbow gradients. The frontend-design skill guides execution.

## 3. Data flow (end to end)

1. App `POST /runs {mode, model}` → backend starts the run thread → returns `run_id`.
2. App opens `WS /runs/{id}/ws`; backend replays current state then streams live.
3. Agent calls the real model + real tools; each step → `event` message.
4. Layer 1 trips → (flag) backend sends `decision_required`, the run **pauses**; (auto) the
   judge's correction is auto-applied and streamed.
5. User taps a button → app sends `intervene` → backend unblocks the run with that decision →
   agent continues or stops.
6. Run ends → `done` with the real summary.

## 4. Error handling

- **Backend:** missing API key / provider error → `400` on `POST /runs` with a clear message;
  model/runtime errors mid-run → `error` WS message + run status `error` (never a crash);
  pause timeout → safe terminate; unknown `run_id` → `404`; each run isolated in the registry.
- **App:** bad URL / unreachable → inline error on "Test connection"; WebSocket drop →
  auto-reconnect and resync via `GET /runs/{id}`; malformed messages ignored defensively.

## 5. Testing

- **Engine hooks:** unit tests that `on_observe` fires per observe and `on_pause` overrides the
  terminal path and its returned decision is honored.
- **Backend (no API key, fully mocked):** an integration test using Starlette's
  `TestClient.websocket_connect` that drives a **complete run with a mocked provider + judge**,
  asserting: `event` messages stream, a `decision_required` is emitted on a flag-mode trip,
  an `intervene` (both `terminate` and `approve`) resumes/stops correctly, and `done` arrives
  with a correct summary. Plus unit tests for `RunRegistry` and the intervention mapping.
  The run thread uses an injected fake provider so tests need no network/key.
- **App:** `tsc --noEmit` typechecks; `expo export --platform web` proves the bundle builds.
  **Honest limitation:** the native mobile UI cannot be visually verified from the build
  environment — the user runs it (iOS simulator or Expo Go) and we polish together.

## 6. Dependencies & layout

- New extra: `loopguard[server] = ["fastapi>=0.110", "uvicorn[standard]>=0.29"]`
  (uvicorn[standard] bundles the WebSocket stack). Core install stays light.
- New CLI: `loopguard serve --host 0.0.0.0 --port 8000`.
- `cloud-app/` — Expo project (TypeScript, native WebSocket, AsyncStorage,
  `@expo-google-fonts/*`). Kept out of the Python package; its own `package.json`.

## 7. Out of scope (this build)

- Connecting to **external** agents (Claude Code / Codex) — a future "LoopGuard plugin" idea.
  The app monitors LoopGuard's own real live agent, which sidesteps all external-agent plumbing.
- Auth, multi-user, persistence beyond in-memory run state, and actual cloud deployment
  (the server is deploy-ready; we just run it locally for the demo).
- Push notifications.

## 8. Demo script

1. Laptop: `loopguard serve` (uvicorn up).
2. App: open in iOS simulator or scan the Expo QR on a phone; point it at the server URL.
3. Tap **Start (flag mode)** → watch the real agent stream tool calls and loop on
   `package.json`.
4. The **decision card** appears with the judge's real reasoning + suggested fix.
5. Tap **Approve fix** → the agent receives the correction and continues (or **Terminate** to
   kill it). Token/$ meter reflects real spend.
6. If asked about cloud: "the same server deploys to a host; here it's on the laptop over LAN."
