from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from .dispatch import DispatchError, EventDispatcher, Handler
from .events import ControlEvent
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


class LoopGuardDaemon:
    def __init__(
        self,
        *,
        store: EventStore,
        socket_path: str | Path | None = None,
        transport: LocalTransport | None = None,
        handlers: Mapping[str, Handler] | None = None,
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
                    "decision": result.decision.model_dump(mode="json"),
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
