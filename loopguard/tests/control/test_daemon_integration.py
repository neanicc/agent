from __future__ import annotations

import asyncio
import time

from loopguard.control.daemon import LoopGuardDaemon
from loopguard.control.dispatch import EventDispatcher, GuardRegistry, HandlerDelivery
from loopguard.control.events import ControlEvent
from loopguard.control.protocol import MessageType
from loopguard.control.store import EventStore

from .daemon_test_support import send_event, short_socket_path, tool_event


def test_duplicate_retry_returns_original_ack_without_double_applying_guard(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(store=store, socket_path=socket_path)
            await daemon.start()
            try:
                original = tool_event("evt-1")
                first = await send_event(socket_path, original, request_id="req-first")
                retry = await send_event(socket_path, original, request_id="req-retry")
                second_unique = await send_event(socket_path, tool_event("evt-2"))
                third_unique = await send_event(socket_path, tool_event("evt-3"))

                assert retry.payload["position"] == first.payload["position"]
                assert retry.payload["decision"] == first.payload["decision"]
                assert second_unique.payload["decision"]["action"] == "allow"
                assert third_unique.payload["decision"]["action"] == "request_approval"
                assert store.count() == 3
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_startup_replays_persisted_unmarked_events_before_accepting_connections(tmp_path):
    async def scenario():
        path = tmp_path / "events.db"
        with short_socket_path() as socket_path:
            store = EventStore.for_test(path)
            store.append(tool_event("evt-1"))
            store.append(tool_event("evt-2"))
            assert store.get_core_decision(1) is None

            daemon = LoopGuardDaemon(store=store, socket_path=socket_path)
            await daemon.start()
            try:
                response = await send_event(socket_path, tool_event("evt-3"))
                assert response.payload["decision"]["action"] == "request_approval"
                assert store.get_core_decision(1) is not None
                assert store.get_core_decision(2) is not None
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_secondary_handler_failure_is_durable_bounded_and_secret_safe(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            calls: list[HandlerDelivery] = []

            async def failing_handler(delivery: HandlerDelivery) -> None:
                calls.append(delivery)
                raise RuntimeError("must-not-leak-handler-secret")

            daemon = LoopGuardDaemon(
                store=store,
                socket_path=socket_path,
                handlers={"verification": failing_handler},
                handler_max_attempts=2,
                handler_retry_delay=0,
            )
            await daemon.start()
            try:
                response = await send_event(socket_path, tool_event("evt-handler"))
                assert response.message_type is MessageType.ACK
                assert response.payload["handlers"]["verification"] in {"queued", "running"}

                await daemon.wait_for_handlers()
                statuses = store.handler_statuses(1)
                assert statuses["verification"] == {
                    "status": "failed",
                    "attempts": 2,
                    "error_code": "handler_failed",
                }
                assert len(calls) == 2
                assert {call.delivery_id for call in calls} == {"verification:1"}
                assert "must-not-leak" not in str(statuses)
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_handlers_receive_redacted_events(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            received: list[ControlEvent] = []

            async def handler(delivery: HandlerDelivery) -> None:
                received.append(delivery.event)

            daemon = LoopGuardDaemon(
                store=store,
                socket_path=socket_path,
                handlers={"relay": handler},
            )
            await daemon.start()
            try:
                await send_event(
                    socket_path,
                    tool_event(
                        "evt-redacted",
                        payload={
                            "agent": "codex",
                            "tool_name": "Bash",
                            "arguments": {"cmd": "echo safe"},
                            "Authorization": "Bearer must-not-reach-handler",
                        },
                    ),
                )
                await daemon.wait_for_handlers()
                assert len(received) == 1
                assert received[0].payload["Authorization"] == "[REDACTED]"
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_active_session_state_is_not_silently_evicted_by_idle_cleanup():
    registry = GuardRegistry()
    registry.observe(tool_event("evt-1"))
    registry.observe(tool_event("evt-2"))
    time.sleep(0.01)

    decision = registry.observe(tool_event("evt-3"))

    assert decision.state_version == 3
    assert decision.action == "request_approval"


def test_wait_for_handlers_ignores_pending_rows_for_unregistered_handlers(tmp_path):
    async def scenario():
        store = EventStore.for_test(tmp_path / "events.db")
        position = store.append(tool_event("evt-existing"))
        store.ensure_handler_deliveries(position.local_log_seq, ("old",))

        async def current_handler(_delivery: HandlerDelivery) -> None:
            return None

        dispatcher = EventDispatcher(store=store, handlers={"current": current_handler})
        await dispatcher.start()
        try:
            await asyncio.wait_for(dispatcher.wait_for_handlers(), timeout=0.2)
            assert store.handler_statuses(1)["old"]["status"] == "queued"
            assert store.handler_statuses(1)["current"]["status"] == "succeeded"
        finally:
            await dispatcher.close()
            store.close()

    asyncio.run(scenario())


def test_restart_does_not_exceed_handler_attempt_budget_after_crash(tmp_path):
    async def scenario():
        store = EventStore.for_test(tmp_path / "events.db")
        position = store.append(tool_event("evt-crashed"))
        store.ensure_handler_deliveries(position.local_log_seq, ("relay",))
        assert store.mark_handler_running("relay", position.local_log_seq) == 1
        calls = 0

        async def handler(_delivery: HandlerDelivery) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("should not run beyond the durable budget")

        dispatcher = EventDispatcher(
            store=store,
            handlers={"relay": handler},
            handler_max_attempts=1,
            handler_retry_delay=0,
        )
        await dispatcher.start()
        try:
            await dispatcher.wait_for_handlers()
            assert calls == 0
            assert store.handler_statuses(1)["relay"] == {
                "status": "failed",
                "attempts": 1,
                "error_code": "handler_failed",
            }
        finally:
            await dispatcher.close()
            store.close()

    asyncio.run(scenario())


def test_bounded_handler_queue_keeps_overflow_durable_until_capacity_returns(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            release = asyncio.Event()
            calls: list[int] = []

            async def slow_handler(delivery: HandlerDelivery) -> None:
                calls.append(delivery.local_log_seq)
                await release.wait()

            daemon = LoopGuardDaemon(
                store=store,
                socket_path=socket_path,
                handlers={"relay": slow_handler},
                handler_queue_size=1,
            )
            await daemon.start()
            try:
                for index in range(1, 4):
                    response = await send_event(
                        socket_path,
                        tool_event(f"evt-{index}", command=f"pytest {index}"),
                    )
                    assert response.message_type is MessageType.ACK
                assert store.count() == 3
                assert store.handler_statuses(3)["relay"]["status"] == "queued"

                release.set()
                await asyncio.wait_for(daemon.wait_for_handlers(), timeout=1)
                assert calls == [1, 2, 3]
                assert all(
                    store.handler_statuses(index)["relay"]["status"] == "succeeded"
                    for index in range(1, 4)
                )
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_succeeded_handler_delivery_is_not_replayed_after_restart(tmp_path):
    async def scenario():
        path = tmp_path / "events.db"
        calls: list[str] = []

        async def handler(delivery: HandlerDelivery) -> None:
            calls.append(delivery.delivery_id)

        with short_socket_path() as first_socket:
            store = EventStore.for_test(path)
            daemon = LoopGuardDaemon(
                store=store,
                socket_path=first_socket,
                handlers={"relay": handler},
            )
            await daemon.start()
            await send_event(first_socket, tool_event("evt-once"))
            await daemon.wait_for_handlers()
            await daemon.close()
            assert calls == ["relay:1"]

        with short_socket_path() as second_socket:
            daemon = LoopGuardDaemon(
                store=store,
                socket_path=second_socket,
                handlers={"relay": handler},
            )
            await daemon.start()
            try:
                await daemon.wait_for_handlers()
                assert calls == ["relay:1"]
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_existing_core_marker_does_not_backfill_a_new_handler(tmp_path):
    async def scenario():
        store = EventStore.for_test(tmp_path / "events.db")
        position = store.append(tool_event("evt-already-dispatched"))
        registry = GuardRegistry()
        store.record_core_dispatch(
            position.local_log_seq,
            registry.observe(store.get_event(position.local_log_seq).event),
        )
        calls = 0

        async def newly_enabled_handler(_delivery: HandlerDelivery) -> None:
            nonlocal calls
            calls += 1

        dispatcher = EventDispatcher(
            store=store,
            handlers={"new": newly_enabled_handler},
        )
        await dispatcher.start()
        try:
            await dispatcher.wait_for_handlers()
            assert calls == 0
            assert "new" not in store.handler_statuses(position.local_log_seq)
        finally:
            await dispatcher.close()
            store.close()

    asyncio.run(scenario())
