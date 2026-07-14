from __future__ import annotations

import asyncio
import os

from loopguard.control.daemon import LoopGuardDaemon
from loopguard.control.protocol import MAGIC, MessageType, encode_frame, read_frame
from loopguard.control.store import EventStore
from loopguard.control.transport import UnixSocketTransport

from .daemon_test_support import send_event, short_socket_path, tool_event


def test_daemon_acknowledges_only_after_persistence_and_core_dispatch(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(store=store, socket_path=socket_path)
            await daemon.start()
            try:
                response = await send_event(socket_path, tool_event("evt-1"))

                assert response.message_type is MessageType.ACK
                assert response.payload["ok"] is True
                assert response.payload["position"] == {
                    "local_log_seq": 1,
                    "repo_seq": 1,
                    "session_seq": 1,
                }
                assert response.payload["decision"]["action"] == "allow"
                assert store.read_local_after(0, 10)[0].event.event_id == "evt-1"
                assert store.get_core_decision(1) is not None
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_repeated_tool_event_reaches_real_guard_and_returns_policy_decision(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(store=store, socket_path=socket_path)
            await daemon.start()
            try:
                responses = [
                    await send_event(socket_path, tool_event(f"evt-{index}"))
                    for index in range(1, 4)
                ]
                decision = responses[-1].payload["decision"]
                assert decision["action"] == "request_approval"
                assert decision["target"] == {"kind": "session", "target_id": "session"}
                assert decision["metadata"]["detector"] == "exact"
                assert decision["state_version"] == 3
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_oversized_frame_is_rejected_without_persistence(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(store=store, socket_path=socket_path, max_frame_bytes=64)
            await daemon.start()
            try:
                reader, writer = await asyncio.open_unix_connection(socket_path)
                valid = encode_frame(MessageType.EVENT, request_id="req", payload={"x": "y"})
                oversized_header = valid[:8] + (65).to_bytes(4, "big")
                writer.write(oversized_header + b"x" * 65)
                await writer.drain()
                response = await read_frame(reader)
                assert response is not None
                assert response.message_type is MessageType.ERROR
                assert response.payload["code"] == "frame_too_large"
                assert store.count() == 0
                writer.close()
                await writer.wait_closed()
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_wrong_peer_is_closed_before_frame_processing(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            transport = UnixSocketTransport(
                socket_path,
                peer_uid_resolver=lambda _socket: os.getuid() + 1,
            )
            daemon = LoopGuardDaemon(store=store, transport=transport)
            await daemon.start()
            try:
                reader, writer = await asyncio.open_unix_connection(socket_path)
                writer.write(MAGIC)
                await writer.drain()
                assert await asyncio.wait_for(reader.read(), timeout=1) == b""
                assert store.count() == 0
                writer.close()
                await writer.wait_closed()
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_concurrent_client_limit_returns_stable_error(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(
                store=store,
                socket_path=socket_path,
                max_concurrent_clients=1,
            )
            await daemon.start()
            first_reader, first_writer = await asyncio.open_unix_connection(socket_path)
            del first_reader
            try:
                await asyncio.sleep(0)
                second_reader, second_writer = await asyncio.open_unix_connection(socket_path)
                try:
                    response = await asyncio.wait_for(read_frame(second_reader), timeout=1)
                    assert response is not None
                    assert response.message_type is MessageType.ERROR
                    assert response.payload == {"code": "server_busy", "ok": False}
                finally:
                    second_writer.close()
                    await second_writer.wait_closed()
            finally:
                first_writer.close()
                await first_writer.wait_closed()
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_idle_connection_is_bounded_by_timeout(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(
                store=store,
                socket_path=socket_path,
                idle_timeout=0.01,
            )
            await daemon.start()
            reader, writer = await asyncio.open_unix_connection(socket_path)
            try:
                response = await asyncio.wait_for(read_frame(reader), timeout=1)
                assert response is not None
                assert response.message_type is MessageType.ERROR
                assert response.payload["code"] == "idle_timeout"
            finally:
                writer.close()
                await writer.wait_closed()
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_unsupported_event_schema_is_rejected_before_persistence(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(store=store, socket_path=socket_path)
            await daemon.start()
            reader, writer = await asyncio.open_unix_connection(socket_path)
            try:
                payload = tool_event("evt-new-schema").model_dump(mode="json")
                payload["schema_version"] = 2
                writer.write(
                    encode_frame(MessageType.EVENT, request_id="req-schema", payload=payload)
                )
                await writer.drain()
                response = await read_frame(reader)
                assert response is not None
                assert response.message_type is MessageType.ERROR
                assert response.payload["code"] == "invalid_event"
                assert store.count() == 0
            finally:
                writer.close()
                await writer.wait_closed()
                await daemon.close()
                store.close()

    asyncio.run(scenario())


def test_daemon_close_disconnects_active_clients(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(
                store=store,
                socket_path=socket_path,
                idle_timeout=30,
            )
            await daemon.start()
            reader, writer = await asyncio.open_unix_connection(socket_path)
            await asyncio.sleep(0.01)
            await daemon.close()
            try:
                assert await asyncio.wait_for(reader.read(), timeout=0.2) == b""
            finally:
                writer.close()
                await writer.wait_closed()
                store.close()

    asyncio.run(scenario())


def test_unexpected_dispatch_failure_returns_secret_safe_stable_error(tmp_path):
    async def scenario():
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(store=store, socket_path=socket_path)

            async def explode(_stored):
                raise RuntimeError("must-not-leak-internal-secret")

            daemon.dispatcher.dispatch = explode
            await daemon.start()
            try:
                response = await send_event(socket_path, tool_event("evt-failure"))
                assert response.message_type is MessageType.ERROR
                assert response.payload == {"code": "internal_error", "ok": False}
                assert "must-not-leak" not in str(response.payload)
                assert store.count() == 1
                assert store.get_core_decision(1) is None
            finally:
                await daemon.close()
                store.close()

    asyncio.run(scenario())
