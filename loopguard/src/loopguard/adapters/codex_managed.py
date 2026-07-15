from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import secrets
import sqlite3
import stat
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from loopguard.adapters.base import (
    AdapterCapabilities,
    AdapterFailure,
    Capability,
    LifecycleError,
    LifecycleErrorCode,
    ManagedRunRequest,
    ManagedStartEvidence,
)
from loopguard.adapters.jsonrpc import (
    JsonRpcClient,
    JsonRpcError,
    JsonRpcProcessExited,
    JsonRpcProtocolError,
    JsonRpcTimeout,
)
from loopguard.control.decisions import (
    ActionKind,
    ActionRequest,
    ActionTarget,
    TargetKind,
)
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.paths import loopguard_home
from loopguard.control.redaction import redact


CODEX_CLIENT_INFO = {"name": "loopguard", "title": "LoopGuard", "version": "0.1.0"}
CODEX_COMMAND = ("codex", "app-server", "--listen", "stdio://")
_MAX_CONTEXT_BYTES = 64 * 1024
_MAX_EVENT_QUEUE = 256
_APPROVAL_TTL = timedelta(minutes=5)
_APPROVAL_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "applyPatchApproval",
        "execCommandApproval",
    }
)
_ACTIVE_STATUSES = frozenset({"starting", "active", "interrupting", "reattached"})
_SCHEMA = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    worktree_root TEXT NOT NULL,
    proof_contract_id TEXT NOT NULL,
    baseline_id TEXT NOT NULL,
    lease_id TEXT NOT NULL,
    thread_id TEXT NOT NULL UNIQUE,
    turn_id TEXT,
    state_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
PRAGMA user_version = 1;
"""


class CodexRpc(Protocol):
    async def request(self, method: str, params: Mapping[str, Any], *, timeout: float) -> Any: ...

    async def notify(self, method: str, params: Mapping[str, Any]) -> None: ...

    async def respond(self, request_id: str | int, result: Mapping[str, Any]) -> None: ...

    async def close(self) -> None: ...


StartAuthorizer = Callable[
    [ManagedRunRequest], ManagedStartEvidence | None | Awaitable[ManagedStartEvidence | None]
]
SessionStatus = Literal[
    "starting", "active", "between_turns", "interrupting", "recovering", "reattached", "orphaned"
]


@dataclass(slots=True)
class _ManagedSession:
    ref: SessionRef
    worktree_root: Path
    proof_contract_id: str
    baseline_id: str
    lease_id: str
    status: SessionStatus
    state_version: int
    lock: asyncio.Lock
    events: asyncio.Queue[ControlEvent | AdapterFailure]
    actions: asyncio.Queue[ActionRequest]


@dataclass(frozen=True, slots=True)
class _PendingApproval:
    request_id: str | int
    method: str
    params: dict[str, Any]


class CodexManagedAdapter:
    capabilities = AdapterCapabilities(
        surface="codex-app-server",
        supported={
            Capability.OBSERVE,
            Capability.START_MANAGED,
            Capability.INJECT_CONTEXT,
            Capability.INTERRUPT,
            Capability.SELECT_MODEL,
            Capability.SELECT_EFFORT,
            Capability.APPROVE_TOOL,
        },
    )

    def __init__(
        self,
        *,
        rpc: CodexRpc | None = None,
        authorize_start: StartAuthorizer | None = None,
        state_path: Path | None = None,
        command: tuple[str, ...] = CODEX_COMMAND,
        initialize_timeout: float = 10.0,
        turn_timeout: float = 120.0,
        control_timeout: float = 10.0,
    ) -> None:
        if not 0.1 <= initialize_timeout <= 120:
            raise ValueError("initialize timeout must be between 0.1 and 120 seconds")
        if not 1 <= turn_timeout <= 1800:
            raise ValueError("turn timeout must be between 1 and 1800 seconds")
        if not 0.1 <= control_timeout <= 120:
            raise ValueError("control timeout must be between 0.1 and 120 seconds")
        self._rpc = rpc
        self._authorize_start = authorize_start
        self._command = command
        self._initialize_timeout = initialize_timeout
        self._turn_timeout = turn_timeout
        self._control_timeout = control_timeout
        self._initialized = False
        self._initialize_lock = asyncio.Lock()
        self._sessions: dict[str, _ManagedSession] = {}
        self._thread_to_session: dict[str, str] = {}
        self._approval_requests: dict[str, _PendingApproval] = {}
        self._early_thread_messages: dict[str, list[dict[str, Any]]] = {}
        self._closed = False
        path = state_path or loopguard_home() / "codex-managed.sqlite3"
        self._store = _SessionStore(path)
        for state in self._store.load_all():
            managed = self._from_record(state)
            self._sessions[managed.ref.session_id] = managed
            self._thread_to_session[managed.ref.session_id] = managed.ref.session_id

    async def start(self, request: ManagedRunRequest) -> SessionRef | AdapterFailure:
        evidence = await self._authorize(request)
        invalid = _validate_evidence(request, evidence)
        if invalid is not None:
            return _failure(
                LifecycleErrorCode.STALE_STATE,
                invalid,
            )
        assert evidence is not None
        try:
            await self._ensure_initialized()
            models = await self._list_models()
            provider_capabilities = await self._call(
                "modelProvider/capabilities/read", {}, timeout=self._initialize_timeout
            )
            _validate_provider_capabilities(provider_capabilities)
            model_error = _validate_model(models, request.model, request.effort)
            if model_error is not None:
                return _failure(LifecycleErrorCode.PROTOCOL_VERSION, model_error)
            thread_result = await self._call(
                "thread/start",
                {
                    "cwd": str(request.worktree_root),
                    "model": request.model,
                    "approvalPolicy": request.permission_policy,
                    "sandbox": request.sandbox,
                },
                timeout=self._initialize_timeout,
            )
            thread_id = _nested_id(thread_result, "thread")
            ref = SessionRef(
                host_id="local",
                repo_id=request.repository_id,
                session_id=thread_id,
                worktree_id=request.worktree_id,
            )
            managed = _ManagedSession(
                ref=ref,
                worktree_root=request.worktree_root,
                proof_contract_id=request.proof_contract_id,
                baseline_id=evidence.baseline_id,
                lease_id=evidence.worktree_lease_id,
                status="starting",
                state_version=1,
                lock=asyncio.Lock(),
                events=asyncio.Queue(maxsize=_MAX_EVENT_QUEUE),
                actions=asyncio.Queue(maxsize=_MAX_EVENT_QUEUE),
            )
            self._register(managed)
            self._persist(managed)
            for message in self._early_thread_messages.pop(thread_id, []):
                await self.handle_server_message(message)
            async with managed.lock:
                turn_result = await self._call(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": request.prompt}],
                        "model": request.model,
                        "effort": request.effort,
                        "cwd": str(request.worktree_root),
                        "approvalPolicy": request.permission_policy,
                        "sandboxPolicy": _sandbox_policy(request.sandbox, request.worktree_root),
                    },
                    timeout=self._turn_timeout,
                )
                turn_id = _nested_id(turn_result, "turn")
                managed.ref = managed.ref.model_copy(update={"turn_id": turn_id})
                managed.status = "active"
                managed.state_version += 1
                self._persist(managed)
                await self._emit(managed, EventKind.SESSION_STARTED, {"managed": True})
                return managed.ref
        except JsonRpcError as exc:
            return _rpc_failure(exc)

    async def attach(self, session: SessionRef) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        if managed.status not in {"recovering", "orphaned"}:
            return None
        async with managed.lock:
            try:
                await self._ensure_initialized()
                result = await self._call(
                    "thread/resume",
                    {
                        "threadId": managed.ref.session_id,
                        "cwd": str(managed.worktree_root),
                    },
                    timeout=self._initialize_timeout,
                )
                if _nested_id(result, "thread") != managed.ref.session_id:
                    raise JsonRpcProtocolError("thread/resume returned a different thread id")
            except JsonRpcError as exc:
                managed.status = "orphaned"
                managed.state_version += 1
                self._persist(managed)
                return _rpc_failure(exc, session_id=session.session_id)
            managed.status = "reattached"
            managed.state_version += 1
            self._persist(managed)
            return None

    async def steer_active_turn(self, session: SessionRef, context: str) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        context_error = _validate_context(context)
        if context_error is not None:
            return _failure(LifecycleErrorCode.PROTOCOL_VERSION, context_error, session.session_id)
        async with managed.lock:
            if managed.status not in {"active", "reattached"} or managed.ref.turn_id is None:
                return _stale(session.session_id)
            try:
                await self._call(
                    "turn/steer",
                    {
                        "threadId": managed.ref.session_id,
                        "expectedTurnId": managed.ref.turn_id,
                        "input": [{"type": "text", "text": context}],
                    },
                    timeout=self._control_timeout,
                )
            except JsonRpcError as exc:
                return _rpc_failure(exc, session_id=session.session_id)
            return None

    async def inject_between_turns(
        self, session: SessionRef, context: str
    ) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        context_error = _validate_context(context)
        if context_error is not None:
            return _failure(LifecycleErrorCode.PROTOCOL_VERSION, context_error, session.session_id)
        async with managed.lock:
            if managed.status != "between_turns" or managed.ref.turn_id is not None:
                return _stale(session.session_id)
            try:
                await self._call(
                    "thread/inject_items",
                    {
                        "threadId": managed.ref.session_id,
                        "items": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": context}],
                            }
                        ],
                    },
                    timeout=self._control_timeout,
                )
            except JsonRpcError as exc:
                return _rpc_failure(exc, session_id=session.session_id)
            return None

    async def inject(self, session: SessionRef, context: str) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        if managed.status in {"active", "reattached"}:
            return await self.steer_active_turn(session, context)
        if managed.status == "between_turns":
            return await self.inject_between_turns(session, context)
        return _stale(session.session_id)

    async def interrupt(self, session: SessionRef) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        async with managed.lock:
            if managed.status not in {"active", "reattached"} or managed.ref.turn_id is None:
                return _stale(session.session_id)
            turn_id = managed.ref.turn_id
            try:
                await self._call(
                    "turn/interrupt",
                    {"threadId": managed.ref.session_id, "turnId": turn_id},
                    timeout=self._control_timeout,
                )
            except JsonRpcError as exc:
                return _rpc_failure(exc, session_id=session.session_id)
            managed.status = "interrupting"
            managed.state_version += 1
            self._persist(managed)
            return None

    async def resolve_action(
        self, session: SessionRef, action: ActionRequest
    ) -> AdapterFailure | None:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            return managed
        pending = self._approval_requests.get(action.action_id)
        if pending is None or action.target.target_id != session.session_id:
            return _stale(session.session_id)
        try:
            action.validate_state(
                current_state_version=managed.state_version,
                current_state_hash=_state_hash(managed),
            )
        except ValueError:
            return _stale(session.session_id)
        if action.expires_at <= datetime.now(timezone.utc):
            self._approval_requests.pop(action.action_id, None)
            return _stale(session.session_id)
        decision = (
            "accept" if action.kind in {ActionKind.APPROVE, ActionKind.CONTINUE_ONCE} else "decline"
        )
        response = _approval_response(pending.method, pending.params, decision)
        try:
            assert self._rpc is not None
            await self._rpc.respond(pending.request_id, response)
        except JsonRpcError as exc:
            return _rpc_failure(exc, session_id=session.session_id)
        self._approval_requests.pop(action.action_id, None)
        await self._emit(
            managed,
            EventKind.ACTION_RESOLVED,
            {
                "action_id": action.action_id,
                "request_method": pending.method,
                "decision": decision,
            },
        )
        return None

    async def next_action(self, session: SessionRef) -> ActionRequest:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            raise LookupError(managed.message)
        return await managed.actions.get()

    async def handle_server_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            raise JsonRpcProtocolError("server message method and params are invalid")
        managed = self._session_for_params(params)
        if managed is None:
            early_thread_id = _thread_id(params)
            if method == "thread/started" and early_thread_id is not None:
                queued = self._early_thread_messages.setdefault(early_thread_id, [])
                if len(queued) >= 4:
                    raise JsonRpcProtocolError("early thread notification limit exceeded")
                queued.append(message)
                return
            if method.startswith(("item/", "turn/", "thread/")):
                raise JsonRpcProtocolError("server message targets an unknown thread")
            return
        if method in _APPROVAL_METHODS and "id" in message:
            await self._handle_approval(managed, message["id"], method, params)
            return
        async with managed.lock:
            turn_id = _message_turn_id(params)
            if turn_id is not None and managed.ref.turn_id not in {None, turn_id}:
                raise JsonRpcProtocolError("server message targets a stale turn")
            if method == "turn/started":
                if turn_id is None:
                    raise JsonRpcProtocolError("turn/started is missing a turn id")
                managed.ref = managed.ref.model_copy(update={"turn_id": turn_id})
                managed.status = "active"
                managed.state_version += 1
                self._persist(managed)
                return
            if method == "turn/completed":
                if turn_id is None or managed.ref.turn_id != turn_id:
                    raise JsonRpcProtocolError("turn/completed does not match the active turn")
                managed.ref = managed.ref.model_copy(update={"turn_id": None})
                managed.status = "between_turns"
                managed.state_version += 1
                self._persist(managed)
                await self._emit(managed, EventKind.TURN_COMPLETED, _bounded_payload(params))
                return
            if method.startswith("item/"):
                kind = _event_kind(method, params)
                if kind is not None:
                    await self._emit(managed, kind, _bounded_payload(params))

    async def events(self, session: SessionRef) -> AsyncIterator[ControlEvent | AdapterFailure]:
        managed = self._owned(session)
        if isinstance(managed, LifecycleError):
            yield managed
            return
        while not self._closed:
            yield await managed.events.get()

    def session_status(self, session_id: str) -> str | None:
        managed = self._sessions.get(session_id)
        return managed.status if managed is not None else None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._rpc is not None:
            await self._rpc.close()
        self._store.close()

    async def _authorize(self, request: ManagedRunRequest) -> ManagedStartEvidence | None:
        if self._authorize_start is None:
            return None
        result = self._authorize_start(request)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            if self._rpc is None:
                self._rpc = await JsonRpcClient.launch(
                    self._command,
                    on_message=self.handle_server_message,
                )
            await self._call(
                "initialize",
                {
                    "clientInfo": CODEX_CLIENT_INFO,
                    "capabilities": {"experimentalApi": False},
                },
                timeout=self._initialize_timeout,
            )
            await self._rpc.notify("initialized", {})
            self._initialized = True

    async def _call(self, method: str, params: Mapping[str, Any], *, timeout: float) -> Any:
        if self._rpc is None:
            raise JsonRpcProcessExited("Codex app-server has not been started")
        result = await self._rpc.request(method, params, timeout=timeout)
        if not isinstance(result, dict):
            raise JsonRpcProtocolError(f"{method} returned a non-object result")
        return result

    async def _list_models(self) -> dict[str, Any]:
        models: list[Any] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _page in range(10):
            params: dict[str, Any] = {}
            if cursor is not None:
                params["cursor"] = cursor
            result = await self._call("model/list", params, timeout=self._initialize_timeout)
            data = result.get("data")
            if not isinstance(data, list):
                raise JsonRpcProtocolError("model/list response is missing its model data")
            models.extend(data)
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return {"data": models}
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                raise JsonRpcProtocolError("model/list returned an invalid pagination cursor")
            seen.add(next_cursor)
            cursor = next_cursor
        raise JsonRpcProtocolError("model/list exceeded the bounded page limit")

    def _register(self, managed: _ManagedSession) -> None:
        if managed.ref.session_id in self._sessions:
            raise JsonRpcProtocolError("Codex returned a duplicate thread id")
        self._sessions[managed.ref.session_id] = managed
        self._thread_to_session[managed.ref.session_id] = managed.ref.session_id

    def _owned(self, session: SessionRef) -> _ManagedSession | LifecycleError:
        managed = self._sessions.get(session.session_id)
        if managed is None or managed.ref.repo_id != session.repo_id:
            return LifecycleError.for_code(
                LifecycleErrorCode.UNKNOWN_SESSION,
                surface=self.capabilities.surface,
                session_id=session.session_id,
            )
        if session.worktree_id != managed.ref.worktree_id:
            return _stale(session.session_id)
        return managed

    def _session_for_params(self, params: Mapping[str, Any]) -> _ManagedSession | None:
        thread_id = _thread_id(params)
        if thread_id is None:
            return None
        session_id = self._thread_to_session.get(thread_id)
        return self._sessions.get(session_id) if session_id is not None else None

    async def _handle_approval(
        self,
        managed: _ManagedSession,
        request_id: str | int,
        method: str,
        params: dict[str, Any],
    ) -> None:
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
            raise JsonRpcProtocolError("approval request id is invalid")
        turn_id = _message_turn_id(params)
        if turn_id is not None and turn_id != managed.ref.turn_id:
            raise JsonRpcProtocolError("approval targets a stale turn")
        digest = hashlib.sha256(
            f"{managed.ref.session_id}\0{request_id}\0{method}".encode()
        ).hexdigest()
        action_id = f"codex:{digest[:32]}"
        action = ActionRequest(
            action_id=action_id,
            target=ActionTarget(kind=TargetKind.SESSION, target_id=managed.ref.session_id),
            kind=ActionKind.APPROVE,
            parameters={
                "request_method": method,
                "vendor_request_id": request_id,
                "vendor_params": _bounded_payload(params),
            },
            expected_state_version=managed.state_version,
            expected_state_hash=_state_hash(managed),
            nonce=secrets.token_urlsafe(24),
            expires_at=datetime.now(timezone.utc) + _APPROVAL_TTL,
        )
        self._approval_requests[action_id] = _PendingApproval(
            request_id=request_id,
            method=method,
            params=dict(params),
        )
        await _queue_put(managed.actions, action)
        await self._emit(
            managed,
            EventKind.ACTION_REQUESTED,
            {"action_request": action.model_dump(mode="json")},
        )

    async def _emit(
        self, managed: _ManagedSession, kind: EventKind, payload: dict[str, Any]
    ) -> None:
        event = ControlEvent(
            event_id="codex:" + secrets.token_hex(16),
            kind=kind,
            source=self.capabilities.surface,
            session=managed.ref,
            payload=payload,
        )
        await _queue_put(managed.events, event)

    def _persist(self, managed: _ManagedSession) -> None:
        self._store.save(managed)

    def _from_record(self, record: sqlite3.Row) -> _ManagedSession:
        status = str(record["status"])
        if status in _ACTIVE_STATUSES:
            status = "recovering"
            self._store.mark_status(str(record["session_id"]), status)
        return _ManagedSession(
            ref=SessionRef(
                host_id="local",
                repo_id=str(record["repo_id"]),
                session_id=str(record["session_id"]),
                worktree_id=str(record["worktree_id"]),
                turn_id=str(record["turn_id"]) if record["turn_id"] is not None else None,
            ),
            worktree_root=Path(str(record["worktree_root"])),
            proof_contract_id=str(record["proof_contract_id"]),
            baseline_id=str(record["baseline_id"]),
            lease_id=str(record["lease_id"]),
            status=status,  # type: ignore[arg-type]
            state_version=int(record["state_version"]),
            lock=asyncio.Lock(),
            events=asyncio.Queue(maxsize=_MAX_EVENT_QUEUE),
            actions=asyncio.Queue(maxsize=_MAX_EVENT_QUEUE),
        )


class _SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()
        _secure_database_path(self.path)
        self._connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self._connection.row_factory = sqlite3.Row
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        objects = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        if version == 0:
            if objects:
                raise ValueError("unversioned Codex session database is not empty")
            self._connection.executescript(_SCHEMA)
        elif version != 1:
            raise ValueError("Codex session database schema is unsupported")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        _secure_sqlite_files(self.path)

    def load_all(self) -> list[sqlite3.Row]:
        return list(self._connection.execute("SELECT * FROM sessions ORDER BY session_id"))

    def save(self, managed: _ManagedSession) -> None:
        self._connection.execute(
            """
            INSERT INTO sessions(
                session_id, repo_id, worktree_id, worktree_root, proof_contract_id,
                baseline_id, lease_id, thread_id, turn_id, state_version, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                turn_id=excluded.turn_id, state_version=excluded.state_version,
                status=excluded.status, updated_at=excluded.updated_at
            """,
            (
                managed.ref.session_id,
                managed.ref.repo_id,
                managed.ref.worktree_id,
                str(managed.worktree_root),
                managed.proof_contract_id,
                managed.baseline_id,
                managed.lease_id,
                managed.ref.session_id,
                managed.ref.turn_id,
                managed.state_version,
                managed.status,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        _secure_sqlite_files(self.path)

    def mark_status(self, session_id: str, status: str) -> None:
        self._connection.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, datetime.now(timezone.utc).isoformat(), session_id),
        )

    def close(self) -> None:
        self._connection.close()


def _validate_evidence(
    request: ManagedRunRequest, evidence: ManagedStartEvidence | None
) -> str | None:
    if evidence is None:
        return "managed start requires proof, baseline, and worktree lease evidence"
    if evidence.proof_contract_id != request.proof_contract_id:
        return "managed start proof contract does not match the request"
    if evidence.worktree_id != request.worktree_id:
        return "managed start worktree lease does not match the request"
    if evidence.worktree_root.resolve() != request.worktree_root.resolve():
        return "managed start worktree root does not match the lease"
    if not evidence.captured_before_first_mutation:
        return "managed start baseline ordering is unproven"
    return None


def _validate_model(result: dict[str, Any], model: str, effort: str) -> str | None:
    values = result.get("data")
    if not isinstance(values, list):
        return "model/list response is missing its model data"
    selected = next(
        (
            value
            for value in values
            if isinstance(value, dict) and model in {value.get("id"), value.get("model")}
        ),
        None,
    )
    if selected is None:
        return f"requested model {model!r} is not advertised by Codex"
    options = selected.get("supportedReasoningEfforts")
    if not isinstance(options, list):
        return "selected model does not advertise reasoning effort support"
    supported = {
        option.get("reasoningEffort", option.get("effort"))
        for option in options
        if isinstance(option, dict)
    }
    if effort not in supported:
        return f"requested effort {effort!r} is not advertised by Codex"
    return None


def _validate_provider_capabilities(result: dict[str, Any]) -> None:
    for name in ("imageGeneration", "namespaceTools", "webSearch"):
        if not isinstance(result.get(name), bool):
            raise JsonRpcProtocolError(
                "modelProvider/capabilities/read returned an unsupported shape"
            )


def _approval_response(
    method: str, params: Mapping[str, Any], decision: Literal["accept", "decline"]
) -> dict[str, Any]:
    if method == "item/permissions/requestApproval":
        permissions = params.get("permissions") if decision == "accept" else {}
        if not isinstance(permissions, dict):
            raise JsonRpcProtocolError("permissions approval is missing requested permissions")
        return {"permissions": permissions, "scope": "turn"}
    if method in {"applyPatchApproval", "execCommandApproval"}:
        return {"decision": "approved" if decision == "accept" else "denied"}
    return {"decision": decision}


def _sandbox_policy(value: str, worktree_root: Path) -> dict[str, Any]:
    normalized = value.replace("-", "").replace("_", "").lower()
    if normalized == "workspacewrite":
        return {
            "type": "workspaceWrite",
            "writableRoots": [str(worktree_root)],
            "networkAccess": False,
        }
    if normalized == "readonly":
        return {"type": "readOnly", "networkAccess": False}
    if normalized in {"dangerfullaccess", "fullaccess"}:
        return {"type": "dangerFullAccess"}
    raise JsonRpcProtocolError(f"unsupported sandbox policy: {value!r}")


def _nested_id(result: dict[str, Any], key: str) -> str:
    value = result.get(key)
    identifier = value.get("id") if isinstance(value, dict) else None
    if not isinstance(identifier, str) or not identifier or len(identifier) > 256:
        raise JsonRpcProtocolError(f"Codex response is missing bounded {key}.id")
    return identifier


def _message_turn_id(params: Mapping[str, Any]) -> str | None:
    turn_id = params.get("turnId")
    turn = params.get("turn")
    if not isinstance(turn_id, str) and isinstance(turn, dict):
        turn_id = turn.get("id")
    return turn_id if isinstance(turn_id, str) else None


def _thread_id(params: Mapping[str, Any]) -> str | None:
    thread_id = params.get("threadId")
    if not isinstance(thread_id, str):
        thread = params.get("thread")
        if isinstance(thread, dict):
            thread_id = thread.get("id")
    if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 256:
        return None
    return thread_id


def _event_kind(method: str, params: Mapping[str, Any]) -> EventKind | None:
    if method == "item/started":
        item = params.get("item")
        item_type = item.get("type") if isinstance(item, dict) else None
        if item_type in {"commandExecution", "fileChange", "mcpToolCall"}:
            return EventKind.TOOL_CALL
    if method == "item/completed":
        return EventKind.TOOL_RESULT
    if method.startswith("item/fileChange/"):
        return EventKind.FILE_CHANGED
    return None


def _bounded_payload(params: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = redact(dict(params))
    assert isinstance(sanitized, dict)
    encoded = str(sanitized)
    if len(encoded.encode()) > _MAX_CONTEXT_BYTES:
        return {"truncated": True, "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
    return sanitized


def _validate_context(context: str) -> str | None:
    if not isinstance(context, str) or not context.strip():
        return "injected context must be non-empty"
    if len(context.encode()) > _MAX_CONTEXT_BYTES:
        return "injected context exceeds the 64 KiB limit"
    return None


def _state_hash(managed: _ManagedSession) -> str:
    value = (
        f"{managed.ref.session_id}\0{managed.ref.turn_id or ''}\0"
        f"{managed.status}\0{managed.state_version}"
    )
    return hashlib.sha256(value.encode()).hexdigest()


async def _queue_put(queue: asyncio.Queue[Any], item: Any) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(item)


def _failure(
    code: LifecycleErrorCode, message: str, session_id: str | None = None
) -> LifecycleError:
    return LifecycleError(
        code=code,
        message=message[:512],
        surface="codex-app-server",
        session_id=session_id,
        retryable=code is LifecycleErrorCode.TIMEOUT,
    )


def _stale(session_id: str) -> LifecycleError:
    return LifecycleError.for_code(
        LifecycleErrorCode.STALE_STATE,
        surface="codex-app-server",
        session_id=session_id,
    )


def _rpc_failure(exc: JsonRpcError, session_id: str | None = None) -> LifecycleError:
    if isinstance(exc, JsonRpcTimeout):
        code = LifecycleErrorCode.TIMEOUT
    elif isinstance(exc, JsonRpcProcessExited):
        code = LifecycleErrorCode.PROCESS_EXITED
    else:
        code = LifecycleErrorCode.PROTOCOL_VERSION
    return _failure(code, str(exc), session_id)


def _secure_database_path(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Codex session database path cannot be a symlink")
    if os.name == "posix":
        parent = os.lstat(path.parent)
        if parent.st_uid != os.getuid():
            raise ValueError("Codex session database parent must be owner controlled")
        os.chmod(path.parent, 0o700)
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if os.name == "posix":
            status = os.fstat(descriptor)
            if status.st_uid != os.getuid() or not stat.S_ISREG(status.st_mode):
                raise ValueError("Codex session database must be an owner file")
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _secure_sqlite_files(path: Path) -> None:
    if os.name != "posix":
        return
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.chmod(0o600)
