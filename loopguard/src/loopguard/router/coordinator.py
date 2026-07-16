from __future__ import annotations

import inspect
import os
import sqlite3
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from loopguard.control.paths import ensure_private_home

from .policy import Phase, RoutingDecision


class PhaseTransitionError(RuntimeError):
    """A phase change is unsafe, invalid, or requires human review."""


class PhaseRouteAdapter(Protocol):
    """Launches the next managed turn/query with a selected route.

    Implementations must use the provider's supported turn/query creation API;
    this is deliberately not a mid-turn model mutation interface.
    """

    async def apply_phase_route(self, session_id: str, model: str, effort: str) -> object: ...

    async def current_phase_route(self, session_id: str) -> tuple[str, str] | None: ...


class PhaseState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=512)
    phase: Phase
    model_id: str = Field(min_length=1, max_length=256)
    effort: str = Field(min_length=1, max_length=64)
    routing_id: str = Field(min_length=1, max_length=512)
    status: Literal["applying", "active", "orphaned"]
    transition_seq: int = Field(ge=1)
    repair_count: int = Field(ge=0)
    tool_active: bool = False
    updated_at: AwareDatetime


class PhaseCoordinator:
    def __init__(self, path: str | Path, *, max_repairs: int = 3) -> None:
        if not 0 <= max_repairs <= 100:
            raise ValueError("maximum repair count is invalid")
        self.max_repairs = max_repairs
        self._store = _PhaseStore(Path(path))

    async def start_phase(
        self,
        session_id: str,
        phase: Phase,
        decision: RoutingDecision,
        adapter: PhaseRouteAdapter,
        *,
        failed_evidence: bool = False,
        repair_workflow: bool = False,
    ) -> PhaseState:
        current = self._store.get(session_id)
        if current is not None and current.status == "orphaned":
            raise PhaseTransitionError("phase state is orphaned and requires human review")
        if current is not None and current.tool_active:
            raise PhaseTransitionError("cannot switch model during an active tool request")
        if current is not None and current.status == "applying":
            raise PhaseTransitionError("incomplete phase transition requires recovery")
        if decision.phase != phase:
            raise PhaseTransitionError("routing decision phase does not match transition target")
        if (
            current is not None
            and current.status == "active"
            and current.phase == phase
            and current.model_id == decision.model_id
            and current.effort == decision.effort
        ):
            return current
        repair_count = _validate_transition(
            current,
            phase,
            failed_evidence=failed_evidence,
            repair_workflow=repair_workflow,
            max_repairs=self.max_repairs,
        )
        safe = getattr(adapter, "phase_switch_safe", None)
        if current is not None and callable(safe):
            safe_result = safe(session_id)
            if inspect.isawaitable(safe_result):
                safe_result = await safe_result
            if not bool(safe_result):
                raise PhaseTransitionError("managed adapter is not between turns")
        pending = PhaseState(
            session_id=session_id,
            phase=phase,
            model_id=decision.model_id,
            effort=decision.effort,
            routing_id=_routing_id(decision, session_id),
            status="applying",
            transition_seq=1 if current is None else current.transition_seq + 1,
            repair_count=repair_count,
            updated_at=datetime.now(timezone.utc),
        )
        self._store.replace(current, pending)
        try:
            result = adapter.apply_phase_route(session_id, decision.model_id, decision.effort)
            if inspect.isawaitable(result):
                result = await result
            if result is not None and result is not True:
                raise PhaseTransitionError(f"adapter rejected phase route: {result}")
        except Exception as exc:
            orphaned = pending.model_copy(
                update={"status": "orphaned", "updated_at": datetime.now(timezone.utc)}
            )
            self._store.replace(pending, orphaned)
            marker = getattr(adapter, "mark_route_orphaned", None)
            if callable(marker):
                marked = marker(session_id)
                if inspect.isawaitable(marked):
                    await marked
            if isinstance(exc, PhaseTransitionError):
                raise
            raise PhaseTransitionError("adapter phase route failed; state orphaned") from exc
        active = pending.model_copy(
            update={"status": "active", "updated_at": datetime.now(timezone.utc)}
        )
        self._store.replace(pending, active)
        return active

    async def recover(self, session_id: str, adapter: PhaseRouteAdapter) -> PhaseState | None:
        state = self._store.get(session_id)
        if state is None or state.status != "applying":
            return state
        observed = adapter.current_phase_route(session_id)
        if inspect.isawaitable(observed):
            observed = await observed
        status = "active" if observed == (state.model_id, state.effort) else "orphaned"
        recovered = state.model_copy(
            update={"status": status, "updated_at": datetime.now(timezone.utc)}
        )
        self._store.replace(state, recovered)
        if status == "orphaned":
            marker = getattr(adapter, "mark_route_orphaned", None)
            if callable(marker):
                marked = marker(session_id)
                if inspect.isawaitable(marked):
                    await marked
        return recovered

    def tool_started(self, session_id: str) -> PhaseState:
        return self._set_tool(session_id, True)

    def tool_completed(self, session_id: str) -> PhaseState:
        return self._set_tool(session_id, False)

    def state(self, session_id: str) -> PhaseState | None:
        return self._store.get(session_id)

    def _set_tool(self, session_id: str, active: bool) -> PhaseState:
        state = self._store.get(session_id)
        if state is None or state.status != "active":
            raise PhaseTransitionError("tool event targets an inactive phase")
        updated = state.model_copy(
            update={"tool_active": active, "updated_at": datetime.now(timezone.utc)}
        )
        self._store.replace(state, updated)
        return updated


def _validate_transition(
    current: PhaseState | None,
    target: Phase,
    *,
    failed_evidence: bool,
    repair_workflow: bool,
    max_repairs: int,
) -> int:
    if current is None:
        if target not in {"plan", "implement"}:
            raise PhaseTransitionError("managed routing must begin in plan or implement")
        return 0
    allowed = {("plan", "implement"), ("implement", "verify"), ("repair", "verify")}
    if (current.phase, target) in allowed:
        return current.repair_count
    if current.phase == "verify" and target == "implement":
        if not failed_evidence or current.repair_count >= max_repairs:
            raise PhaseTransitionError(
                "verify-to-implement requires failed evidence and repair budget"
            )
        return current.repair_count + 1
    if target == "repair":
        if not repair_workflow or current.repair_count >= max_repairs:
            raise PhaseTransitionError(
                "repair phase requires an authorized bounded repair workflow"
            )
        return current.repair_count + 1
    raise PhaseTransitionError(f"invalid phase transition: {current.phase}->{target}")


def _routing_id(decision: RoutingDecision, session_id: str) -> str:
    return f"{session_id}:{decision.policy_version}:{decision.phase}:{decision.matched_rule}"


class _PhaseStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()
        ensure_private_home(self.path.parent)
        if self.path.is_symlink():
            raise PhaseTransitionError("phase database cannot be a symlink")
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS phases("
            "session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, "
            "transition_seq INTEGER NOT NULL, status TEXT NOT NULL)"
        )
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(phases)").fetchall()
        }
        if columns != {"session_id", "state_json", "transition_seq", "status"}:
            raise PhaseTransitionError("phase database schema is incompatible")
        self._connection.execute("PRAGMA user_version=1")
        self._connection.commit()
        if os.name == "posix":
            os.chmod(self.path, 0o600)
        status = os.lstat(self.path)
        if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
            raise PhaseTransitionError("phase database path is unsafe")
        if os.name == "posix" and (
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise PhaseTransitionError("phase database must be owner-only")

    def get(self, session_id: str) -> PhaseState | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT state_json FROM phases WHERE session_id = ?", (session_id,)
            ).fetchone()
        return None if row is None else PhaseState.model_validate_json(row[0])

    def replace(self, previous: PhaseState | None, state: PhaseState) -> None:
        with self._lock:
            if previous is None:
                try:
                    self._connection.execute(
                        "INSERT INTO phases(session_id,state_json,transition_seq,status) "
                        "VALUES (?,?,?,?)",
                        (
                            state.session_id,
                            state.model_dump_json(),
                            state.transition_seq,
                            state.status,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise PhaseTransitionError("phase transition lost a concurrent race") from exc
            else:
                result = self._connection.execute(
                    "UPDATE phases SET state_json=?, transition_seq=?, status=? "
                    "WHERE session_id=? AND state_json=? AND transition_seq=? AND status=?",
                    (
                        state.model_dump_json(),
                        state.transition_seq,
                        state.status,
                        previous.session_id,
                        previous.model_dump_json(),
                        previous.transition_seq,
                        previous.status,
                    ),
                )
                if result.rowcount != 1:
                    self._connection.rollback()
                    raise PhaseTransitionError("phase transition lost a concurrent race")
            self._connection.commit()
