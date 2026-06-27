from __future__ import annotations

import asyncio
import threading
import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import Callable

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..judge import LLMJudge
from ..providers import make_provider
from .projects import get_project, list_projects, workspace_listing
from .runs import RunRegistry
from .schemas import InterveneRequest, StartRunRequest

_VALID_ACTIONS = {"terminate", "approve", "inject", "continue_once", "allowlist"}

try:
    _VERSION = version("loopguard")
except PackageNotFoundError:  # pragma: no cover - not installed as a dist
    _VERSION = "0.0.0"


def create_app(provider_factory: Callable | None = None,
               judge_factory: Callable | None = None) -> FastAPI:
    app = FastAPI(title="LoopGuard Cloud")
    # The mobile app and Expo web run on a different origin; allow them through.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    registry = RunRegistry()
    subscribers: dict[str, set[WebSocket]] = {}
    state: dict = {"loop": None}

    make_prov = provider_factory or (lambda model, provider: make_provider(model, provider))
    make_judge = judge_factory or (lambda provider, context=None: LLMJudge(provider, context=context))

    def _ensure_loop() -> None:
        # Capture the running loop the first time any request is handled, so the
        # worker thread can schedule broadcasts onto it (robust without lifespan).
        if state["loop"] is None:
            state["loop"] = asyncio.get_running_loop()

    def emit_for(run_id: str) -> Callable[[dict], None]:
        # Thread-safe: schedule the broadcast on the app's event loop.
        def emit(message: dict) -> None:
            loop = state["loop"]
            if loop is None:
                return
            asyncio.run_coroutine_threadsafe(_broadcast(run_id, message), loop)

        return emit

    async def _broadcast(run_id: str, message: dict) -> None:
        for ws in list(subscribers.get(run_id, set())):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - drop dead sockets
                subscribers.get(run_id, set()).discard(ws)

    def _spawn(run_id: str, req: StartRunRequest):
        project = get_project(req.project_id)
        if project is None:
            raise ValueError(f"unknown project {req.project_id!r}")
        provider = make_prov(req.model, req.provider)  # may raise -> caller maps to 400
        judge = make_judge(provider, workspace_listing(project))
        run = registry.create(run_id, project, req.mode, req.model, emit_for(run_id),
                              task=req.task)
        threading.Thread(target=run.execute, args=(provider, judge), daemon=True).start()
        return run

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": _VERSION}

    @app.get("/projects")
    async def projects():
        return list_projects()

    @app.post("/runs")
    async def start_run(req: StartRunRequest):
        _ensure_loop()
        run_id = uuid.uuid4().hex[:12]
        try:
            _spawn(run_id, req)
        except Exception as exc:  # noqa: BLE001 - bad key/provider/project -> 400
            raise HTTPException(status_code=400, detail=str(exc))
        return {"run_id": run_id}

    @app.get("/runs")
    async def list_runs():
        return registry.list()

    @app.get("/allowlist")
    async def allowlist():
        return registry.allowlist()

    @app.get("/autofixes")
    async def autofixes():
        return registry.autofixes()

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str):
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        s = run.state
        return {
            "id": s.id, "project_id": s.project_id, "label": s.label, "kind": s.kind,
            "mode": s.mode, "model": s.model, "task": s.task, "agents": s.agents,
            "status": s.status, "events": s.events, "summary": s.summary,
            "pending": s.pending, "auto_actions": s.auto_actions,
            "allowlist_log": s.allowlist_log, "allowlist": s.allowlist,
            "final_text": s.final_text, "stopped_by_guard": s.stopped_by_guard,
            "error": s.error,
        }

    @app.post("/runs/{run_id}/intervene")
    async def intervene(run_id: str, req: InterveneRequest):
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        run.intervene(req.action, req.message)
        return {"ok": True}

    @app.websocket("/runs/{run_id}/ws")
    async def run_ws(ws: WebSocket, run_id: str, start: str | None = None,
                     project_id: str = "npm-manifest",
                     model: str = "cerebras/gpt-oss-120b"):
        await ws.accept()
        _ensure_loop()
        subscribers.setdefault(run_id, set()).add(ws)
        if start in ("flag", "auto") and registry.get(run_id) is None:
            try:
                _spawn(run_id, StartRunRequest(project_id=project_id, mode=start, model=model))
            except Exception as exc:  # noqa: BLE001
                await ws.send_json({"type": "error", "data": {"message": str(exc)}})
        else:
            run = registry.get(run_id)
            if run is not None:
                for ev in run.state.events:  # replay current state on (re)connect
                    await ws.send_json({"type": "event", "data": ev})
                if run.state.pending is not None:
                    await ws.send_json({"type": "decision_required", "data": run.state.pending})
        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "intervene" and msg.get("action") in _VALID_ACTIONS:
                    run = registry.get(run_id)
                    if run is not None:
                        run.intervene(msg.get("action"), msg.get("message"))
        except WebSocketDisconnect:
            subscribers.get(run_id, set()).discard(ws)
        except Exception:  # noqa: BLE001 - ignore malformed client messages
            subscribers.get(run_id, set()).discard(ws)

    return app
