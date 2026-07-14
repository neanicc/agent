from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from loopguard.config import LoopGuardConfig
from loopguard.decision import LoopDecision
from loopguard.guard import LoopGuard

from .decisions import ActionTarget, PolicyDecision, TargetKind
from .events import ControlEvent, EventKind
from .projection import to_loop_event
from .store import EventStore, StoredEvent


class DispatchError(Exception):
    """Core dispatch could not preserve its deterministic state contract."""


@dataclass(frozen=True, slots=True)
class HandlerDelivery:
    delivery_id: str
    handler_name: str
    event: ControlEvent
    local_log_seq: int


Handler = Callable[[HandlerDelivery], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class DispatchResult:
    decision: PolicyDecision
    handlers: dict[str, str]


@dataclass(slots=True)
class _GuardState:
    guard: LoopGuard
    version: int
    state_hash: str


class GuardRegistry:
    """Isolate active detector state and evict it only on explicit completion."""

    def __init__(self) -> None:
        self._states: dict[str, _GuardState] = {}

    def observe(self, event: ControlEvent) -> PolicyDecision:
        session_id = event.session.session_id
        state = self._states.get(session_id)
        if state is None:
            state = _GuardState(
                guard=_new_guard(),
                version=0,
                state_hash=hashlib.sha256(b"loopguard-control-state-v1").hexdigest(),
            )
            self._states[session_id] = state
        loop_event = to_loop_event(event)
        if loop_event is None:
            loop_decision = LoopDecision(reason="event not eligible for loop detection")
        else:
            loop_decision = state.guard.observe(loop_event)

        state.version += 1
        state.state_hash = _next_state_hash(state.state_hash, event)
        decision = _policy_decision(
            event=event,
            loop_decision=loop_decision,
            state_version=state.version,
            state_hash=state.state_hash,
        )
        if event.kind is EventKind.SESSION_STOPPED:
            self._states.pop(session_id, None)
        return decision


class EventDispatcher:
    def __init__(
        self,
        *,
        store: EventStore,
        handlers: Mapping[str, Handler] | None = None,
        registry: GuardRegistry | None = None,
        handler_queue_size: int = 128,
        handler_max_attempts: int = 3,
        handler_retry_delay: float = 0.05,
    ) -> None:
        if handler_queue_size <= 0 or handler_max_attempts <= 0:
            raise ValueError("handler queue size and attempt limit must be positive")
        if handler_retry_delay < 0:
            raise ValueError("handler retry delay must not be negative")
        self.store = store
        self.handlers = dict(handlers or {})
        self.registry = registry or GuardRegistry()
        self.handler_queue_size = handler_queue_size
        self.handler_max_attempts = handler_max_attempts
        self.handler_retry_delay = handler_retry_delay
        self._queues = {
            name: asyncio.Queue[int](maxsize=handler_queue_size)
            for name in self.handlers
        }
        self._queued = {name: set() for name in self.handlers}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self._replay_core_state()
        self._started = True
        for name in self.handlers:
            self._workers[name] = asyncio.create_task(
                self._handler_worker(name),
                name=f"loopguard-handler-{name}",
            )
            self._refill_handler(name)

    async def dispatch(self, stored: StoredEvent) -> DispatchResult:
        if not self._started:
            raise DispatchError("dispatcher is not started")
        decision = self.store.get_core_decision(stored.local_log_seq)
        if decision is None:
            decision = self.registry.observe(stored.event)
            decision = self.store.record_core_dispatch(
                stored.local_log_seq,
                decision,
                tuple(self.handlers),
            )
        persisted_statuses = self.store.handler_statuses(stored.local_log_seq)
        statuses = {
            name: str(persisted_statuses[name]["status"])
            for name in self.handlers
            if name in persisted_statuses
        }
        for name in self.handlers:
            self._refill_handler(name)
        return DispatchResult(decision=decision, handlers=statuses)

    async def wait_for_handlers(self) -> None:
        if not self.handlers:
            return
        while True:
            await asyncio.gather(*(queue.join() for queue in self._queues.values()))
            if not self.store.has_pending_handler_deliveries(tuple(self.handlers)):
                return
            for name in self.handlers:
                self._refill_handler(name)
            await asyncio.sleep(0)

    async def close(self) -> None:
        workers = list(self._workers.values())
        self._workers.clear()
        self._started = False
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    async def _replay_core_state(self) -> None:
        cursor = 0
        while True:
            batch = self.store.read_local_after(cursor, 256)
            if not batch:
                return
            for stored in batch:
                reconstructed = self.registry.observe(stored.event)
                existing = self.store.get_core_decision(stored.local_log_seq)
                if existing is None:
                    self.store.record_core_dispatch(
                        stored.local_log_seq,
                        reconstructed,
                        tuple(self.handlers),
                    )
                elif (
                    existing.state_version != reconstructed.state_version
                    or existing.state_hash != reconstructed.state_hash
                ):
                    raise DispatchError("durable core state does not replay deterministically")
                cursor = stored.local_log_seq

    def _refill_handler(self, handler_name: str) -> None:
        queue = self._queues[handler_name]
        queued = self._queued[handler_name]
        available = queue.maxsize - queue.qsize()
        if available <= 0:
            return
        pending = self.store.pending_handler_deliveries(handler_name, queue.maxsize)
        for state in pending:
            if state.local_log_seq in queued:
                continue
            try:
                queue.put_nowait(state.local_log_seq)
            except asyncio.QueueFull:
                return
            queued.add(state.local_log_seq)
            available -= 1
            if available == 0:
                return

    async def _handler_worker(self, handler_name: str) -> None:
        queue = self._queues[handler_name]
        queued = self._queued[handler_name]
        handler = self.handlers[handler_name]
        while True:
            local_log_seq = await queue.get()
            try:
                await self._deliver(handler_name, handler, local_log_seq)
            finally:
                queued.discard(local_log_seq)
                self._refill_handler(handler_name)
                queue.task_done()

    async def _deliver(
        self,
        handler_name: str,
        handler: Handler,
        local_log_seq: int,
    ) -> None:
        stored = self.store.get_event(local_log_seq)
        if stored is None:
            raise DispatchError("durable handler delivery references a missing event")
        delivery = HandlerDelivery(
            delivery_id=f"{handler_name}:{local_log_seq}",
            handler_name=handler_name,
            event=stored.event,
            local_log_seq=local_log_seq,
        )
        while True:
            state = self.store.get_handler_delivery(handler_name, local_log_seq)
            if state is None:
                raise DispatchError("durable handler delivery is missing")
            if state.attempts >= self.handler_max_attempts:
                self.store.mark_handler_exhausted(
                    handler_name,
                    local_log_seq,
                    max_attempts=self.handler_max_attempts,
                )
                return
            attempts = self.store.mark_handler_running(handler_name, local_log_seq)
            try:
                result = handler(delivery)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                retry = attempts < self.handler_max_attempts
                self.store.mark_handler_failed(
                    handler_name,
                    local_log_seq,
                    retry=retry,
                    error_code="handler_failed",
                )
                if not retry:
                    return
                if self.handler_retry_delay:
                    await asyncio.sleep(self.handler_retry_delay)
                continue
            self.store.mark_handler_succeeded(handler_name, local_log_seq)
            return


def _new_guard() -> LoopGuard:
    config = LoopGuardConfig(action="pause", enable_judge=False)

    def noninteractive_pause(decision: LoopDecision) -> LoopDecision:
        decision.allowed = False
        decision.developer_action = "none"
        return decision

    return LoopGuard(config=config, on_pause=noninteractive_pause)


def _next_state_hash(previous_hash: str, event: ControlEvent) -> str:
    canonical = json.dumps(
        event.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(f"{previous_hash}\n{canonical}".encode()).hexdigest()


def _policy_decision(
    *,
    event: ControlEvent,
    loop_decision: LoopDecision,
    state_version: int,
    state_hash: str,
) -> PolicyDecision:
    action = "request_approval" if loop_decision.tripped else "allow"
    metadata: dict[str, object] = {}
    if loop_decision.detector is not None:
        metadata["detector"] = loop_decision.detector
    if loop_decision.similarity is not None:
        metadata["similarity"] = loop_decision.similarity
    return PolicyDecision(
        decision_id=(
            f"decision:{event.session.session_id}:{state_version}:{state_hash[:16]}"
        ),
        action=action,
        reason=loop_decision.reason,
        target=ActionTarget(
            kind=TargetKind.SESSION,
            target_id=event.session.session_id,
        ),
        state_version=state_version,
        state_hash=state_hash,
        metadata=metadata,
    )
