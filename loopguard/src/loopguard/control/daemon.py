from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from .dispatch import DispatchError, EventDispatcher, Handler
from .decisions import PolicyDecision, TargetKind
from .events import ControlEvent, EventKind
from .projection import to_loop_event
from .protocol import (
    DEFAULT_MAX_FRAME_BYTES,
    MessageType,
    ProtocolError,
    encode_frame,
    read_frame,
)
from .store import EventIdConflictError, EventStore, EventStoreError
from .transport import LocalTransport, UnixSocketTransport


def _is_potentially_mutating_tool(event: ControlEvent) -> bool:
    tool_name = event.payload.get("tool_name")
    if not isinstance(tool_name, str):
        return True
    normalized = tool_name.strip().lower().replace("-", "_")
    return normalized not in {
        "glob",
        "grep",
        "list",
        "ls",
        "read",
        "search",
        "view_image",
    }


class CompletionPolicy(Protocol):
    def policy_decision(
        self,
        event: ControlEvent,
        core_decision: PolicyDecision,
    ) -> PolicyDecision: ...


class VerificationCoordinator(Protocol):
    proof_intake: object
    runner: object
    verification: object
    completion: CompletionPolicy

    def is_registered(self, session_id: str) -> bool: ...

    async def before_mutation(self, event: ControlEvent): ...

    async def submit_prompt(self, *args, **kwargs): ...

    async def verify_completion(self, *args, **kwargs): ...


@dataclass(frozen=True, slots=True)
class DaemonServices:
    """Production service registry without changing durable detector replay semantics."""

    proof_intake: object | None = None
    runner: object | None = None
    artifact_store: object | None = None
    verdict_service: object | None = None
    completion_enforcement: CompletionPolicy | None = None
    verification_workflow: VerificationCoordinator | None = None
    change_journal: object | None = None
    change_watcher: object | None = None
    symbol_index: object | None = None
    lease_manager: object | None = None
    digest_service: object | None = None
    handoff_store: object | None = None
    worktree_manager: object | None = None
    context_mcp_launcher: object | None = None
    routing_evaluator: object | None = None
    routing_outcome_recorder: object | None = None
    experiment_assigner: object | None = None
    model_catalog: object | None = None
    feature_extractor: object | None = None
    routing_budget: object | None = None
    phase_coordinator: object | None = None
    preference_service: object | None = None
    preference_compiler: object | None = None
    preference_store: object | None = None
    preference_learner: object | None = None
    preference_evaluators: object | None = None
    visual_critic: object | None = None
    attached_collision_policy: str = "warn"

    @classmethod
    def from_verification_workflow(
        cls,
        workflow: VerificationCoordinator,
    ) -> DaemonServices:
        verification = workflow.verification
        return cls(
            proof_intake=workflow.proof_intake,
            runner=workflow.runner,
            artifact_store=getattr(verification, "artifacts", None),
            verdict_service=verification,
            completion_enforcement=workflow.completion,
            verification_workflow=workflow,
        )

    async def submit_prompt(self, *args, **kwargs):
        if self.verification_workflow is None:
            raise ValueError("verification workflow is not configured")
        return await self.verification_workflow.submit_prompt(*args, **kwargs)

    async def verify_completion(self, *args, **kwargs):
        if self.verification_workflow is None:
            raise ValueError("verification workflow is not configured")
        return await self.verification_workflow.verify_completion(*args, **kwargs)

    def context_services(self):
        required = {
            "journal": self.change_journal,
            "leases": self.lease_manager,
            "handoffs": self.handoff_store,
            "digest": self.digest_service,
        }
        if any(service is None for service in required.values()):
            raise ValueError("context services are not fully configured")
        from loopguard.context.mcp_server import ContextServices

        return ContextServices(
            **required,
            symbol_index=self.symbol_index,
            watcher=self.change_watcher,
            worktree_manager=self.worktree_manager,
            mcp_launcher=self.context_mcp_launcher,
            verification=self.verdict_service,
        )

    async def apply_policy(
        self,
        event: ControlEvent,
        core_decision: PolicyDecision,
    ) -> PolicyDecision:
        attachment_failed = self._observe_attached_session(event)
        workflow = self.verification_workflow
        if (
            workflow is not None
            and event.kind is EventKind.TOOL_CALL
            and workflow.is_registered(event.session.session_id)
        ):
            try:
                gate = await workflow.before_mutation(event)
            except Exception:
                decision = core_decision.model_copy(
                    update={
                        "decision_id": f"verification-intake:{event.event_id}",
                        "action": "pause",
                        "reason": "Verification intake failed before mutation.",
                        "metadata": {
                            **core_decision.metadata,
                            "verification_intake": {
                                "status": "inconclusive",
                                "reason": "mutation_gate_failed",
                            },
                        },
                    }
                )
                return self._validate(event, core_decision, decision)
            if not gate.allow:
                decision = core_decision.model_copy(
                    update={
                        "decision_id": f"verification-intake:{event.event_id}",
                        "action": "pause",
                        "reason": f"Verification blocked mutation: {gate.reason}.",
                        "metadata": {
                            **core_decision.metadata,
                            "verification_intake": {
                                "status": gate.status,
                                "reason": gate.reason,
                            },
                        },
                    }
                )
                return self._validate(event, core_decision, decision)
        collision_decision = self._attached_collision_policy(
            event,
            core_decision,
            attachment_failed=attachment_failed,
        )
        if collision_decision is not None:
            core_decision = collision_decision
        if self.completion_enforcement is None:
            return core_decision
        decision = self.completion_enforcement.policy_decision(event, core_decision)
        return self._validate(event, core_decision, decision)

    def _observe_attached_session(self, event: ControlEvent) -> bool:
        manager = self.worktree_manager
        worktree_id = event.session.worktree_id
        if manager is None or worktree_id is None:
            return False
        try:
            if event.kind is EventKind.SESSION_STOPPED:
                manager.detach(event.session.repo_id, event.session.session_id)
            elif event.kind in {EventKind.SESSION_STARTED, EventKind.TOOL_CALL}:
                manager.attach(
                    event.session.repo_id,
                    event.session.session_id,
                    worktree_id,
                )
        except Exception:
            return True
        return False

    def _attached_collision_policy(
        self,
        event: ControlEvent,
        core_decision: PolicyDecision,
        *,
        attachment_failed: bool,
    ) -> PolicyDecision | None:
        manager = self.worktree_manager
        worktree_id = event.session.worktree_id
        if (
            manager is None
            or event.kind is not EventKind.TOOL_CALL
            or worktree_id is None
            or not _is_potentially_mutating_tool(event)
        ):
            return None
        collision = None
        if not attachment_failed:
            try:
                collision = manager.attached_mutation_decision(
                    event.session.repo_id,
                    event.session.session_id,
                    worktree_id,
                    policy=self.attached_collision_policy,
                )
            except Exception:
                attachment_failed = True
        if attachment_failed:
            action = core_decision.action
            if action == "allow":
                action = "pause" if self.attached_collision_policy == "block" else "warn"
            return core_decision.model_copy(
                update={
                    "decision_id": f"worktree-collision-unavailable:{event.event_id}",
                    "action": action,
                    "reason": "Attached worktree collision state is temporarily unavailable.",
                    "metadata": {
                        **core_decision.metadata,
                        "worktree_collision": {"status": "unavailable"},
                    },
                }
            )
        if collision is None or not collision.collision:
            return None
        action = core_decision.action
        if action == "allow":
            action = "warn" if collision.action == "warn" else "pause"
        return core_decision.model_copy(
            update={
                "decision_id": f"worktree-collision:{event.event_id}",
                "action": action,
                "reason": collision.reason,
                "metadata": {
                    **core_decision.metadata,
                    "worktree_collision": collision.model_dump(mode="json"),
                },
            }
        )

    @staticmethod
    def _validate(
        event: ControlEvent,
        core_decision: PolicyDecision,
        decision: PolicyDecision,
    ) -> PolicyDecision:
        if (
            decision.target.kind is not TargetKind.SESSION
            or decision.target.target_id != event.session.session_id
            or decision.state_version != core_decision.state_version
            or decision.state_hash != core_decision.state_hash
        ):
            raise DispatchError("policy overlay violated the core state contract")
        return decision


class LoopGuardDaemon:
    def __init__(
        self,
        *,
        store: EventStore,
        socket_path: str | Path | None = None,
        transport: LocalTransport | None = None,
        handlers: Mapping[str, Handler] | None = None,
        services: DaemonServices | None = None,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
        max_concurrent_clients: int = 32,
        idle_timeout: float = 30.0,
        write_timeout: float = 5.0,
        handler_queue_size: int = 128,
        handler_max_attempts: int = 3,
        handler_retry_delay: float = 0.05,
    ) -> None:
        if (socket_path is None) == (transport is None):
            raise ValueError("provide exactly one of socket_path or transport")
        if max_frame_bytes <= 0 or max_concurrent_clients <= 0:
            raise ValueError("daemon limits must be positive")
        if idle_timeout <= 0 or write_timeout <= 0:
            raise ValueError("daemon timeouts must be positive")
        self.store = store
        self.services = services or DaemonServices()
        self.transport = transport or UnixSocketTransport(Path(socket_path))
        self.max_frame_bytes = max_frame_bytes
        self.max_concurrent_clients = max_concurrent_clients
        self.idle_timeout = idle_timeout
        self.write_timeout = write_timeout
        self.dispatcher = EventDispatcher(
            store=store,
            handlers=handlers,
            handler_queue_size=handler_queue_size,
            handler_max_attempts=handler_max_attempts,
            handler_retry_delay=handler_retry_delay,
        )
        self._active_clients = 0
        self._client_writers: set[asyncio.StreamWriter] = set()
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.dispatcher.start()
        try:
            await self.transport.start(self._handle_connection)
        except Exception:
            await self.dispatcher.close()
            raise
        self._started = True

    async def close(self) -> None:
        if not self._started:
            await self.dispatcher.close()
            return
        self._started = False
        await self.transport.close()
        writers = list(self._client_writers)
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(
                *(writer.wait_closed() for writer in writers),
                return_exceptions=True,
            )
        current = asyncio.current_task()
        client_tasks = [task for task in self._client_tasks if task is not current]
        for task in client_tasks:
            task.cancel()
        if client_tasks:
            await asyncio.gather(*client_tasks, return_exceptions=True)
        await self.dispatcher.close()

    async def wait_for_handlers(self) -> None:
        await self.dispatcher.wait_for_handlers()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if self._active_clients >= self.max_concurrent_clients:
            await self._send_error(writer, "server_busy", request_id="server-busy")
            return
        self._active_clients += 1
        self._client_writers.add(writer)
        current_task = asyncio.current_task()
        if current_task is not None:
            self._client_tasks.add(current_task)
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(
                        read_frame(reader, max_frame_bytes=self.max_frame_bytes),
                        timeout=self.idle_timeout,
                    )
                except TimeoutError:
                    await self._send_error(writer, "idle_timeout", request_id="idle-timeout")
                    return
                except ProtocolError as exc:
                    await self._send_error(
                        writer,
                        exc.code,
                        request_id=exc.request_id or "protocol-error",
                    )
                    return
                if frame is None:
                    return
                if frame.message_type is not MessageType.EVENT:
                    await self._send_error(
                        writer,
                        "unexpected_message_type",
                        request_id=frame.request_id,
                    )
                    return
                try:
                    event = ControlEvent.model_validate(frame.payload)
                    to_loop_event(event)
                except (ValidationError, ValueError, TypeError):
                    await self._send_error(
                        writer,
                        "invalid_event",
                        request_id=frame.request_id,
                    )
                    continue
                try:
                    position = self.store.append(event)
                    stored = self.store.get_event(position.local_log_seq)
                    if stored is None:
                        raise EventStoreError("persisted event is unavailable")
                    result = await self.dispatcher.dispatch(stored)
                    decision = await self.services.apply_policy(
                        stored.event,
                        result.decision,
                    )
                except EventIdConflictError:
                    await self._send_error(
                        writer,
                        "event_id_conflict",
                        request_id=frame.request_id,
                    )
                    continue
                except EventStoreError:
                    await self._send_error(
                        writer,
                        "store_error",
                        request_id=frame.request_id,
                    )
                    continue
                except DispatchError:
                    await self._send_error(
                        writer,
                        "core_dispatch_failed",
                        request_id=frame.request_id,
                    )
                    continue
                except Exception:
                    await self._send_error(
                        writer,
                        "internal_error",
                        request_id=frame.request_id,
                    )
                    continue
                response = {
                    "ok": True,
                    "position": {
                        "local_log_seq": position.local_log_seq,
                        "repo_seq": position.repo_seq,
                        "session_seq": position.session_seq,
                    },
                    "decision": decision.model_dump(mode="json"),
                    "handlers": result.handlers,
                }
                await self._write(
                    writer,
                    MessageType.ACK,
                    request_id=frame.request_id,
                    payload=response,
                )
        finally:
            self._client_writers.discard(writer)
            if current_task is not None:
                self._client_tasks.discard(current_task)
            self._active_clients -= 1

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        code: str,
        *,
        request_id: str,
    ) -> None:
        await self._write(
            writer,
            MessageType.ERROR,
            request_id=request_id,
            payload={"ok": False, "code": code},
        )

    async def _write(
        self,
        writer: asyncio.StreamWriter,
        message_type: MessageType,
        *,
        request_id: str,
        payload: dict[str, object],
    ) -> None:
        try:
            encoded = encode_frame(
                message_type,
                request_id=request_id,
                payload=payload,
            )
            writer.write(encoded)
            await asyncio.wait_for(writer.drain(), timeout=self.write_timeout)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
