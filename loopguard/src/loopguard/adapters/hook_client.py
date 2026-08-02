from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic import ValidationError

from loopguard.control.decisions import PolicyDecision, TargetKind
from loopguard.control.events import ControlEvent
from loopguard.control.paths import ControlPaths
from loopguard.control.protocol import MessageType, ProtocolError, encode_frame, read_frame


DEFAULT_HOOK_TIMEOUT_SECONDS = 0.1
MAX_HOOK_RESPONSE_BYTES = 131_072


class HookClientError(Exception):
    """A bounded local hook round trip failed without exposing internal details."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class HookClient:
    def __init__(
        self,
        *,
        home: str | Path | None = None,
        paths: ControlPaths | None = None,
        timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
    ) -> None:
        if paths is not None and home is not None:
            raise ValueError("provide either trusted control paths or a home override, not both")
        if timeout_seconds <= 0 or timeout_seconds > 1:
            raise ValueError("hook timeout must be positive and no greater than one second")
        self.paths = paths or ControlPaths.from_home(home)
        self.timeout_seconds = timeout_seconds

    def send(self, event: ControlEvent) -> PolicyDecision:
        if os.name != "posix":
            raise HookClientError("transport_capability_unavailable")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise HookClientError("async_context_unsupported")
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._round_trip(event),
                    timeout=self.timeout_seconds,
                )
            )
        except TimeoutError as exc:
            raise HookClientError("daemon_timeout", retryable=True) from exc
        except (ConnectionError, FileNotFoundError, OSError) as exc:
            raise HookClientError("daemon_unreachable", retryable=True) from exc
        except ProtocolError as exc:
            raise HookClientError("daemon_protocol_error") from exc

    async def _round_trip(self, event: ControlEvent) -> PolicyDecision:
        request_id = f"hook-{event.event_id.rsplit(':', 1)[-1][:32]}"
        reader, writer = await asyncio.open_unix_connection(self.paths.socket)
        try:
            writer.write(
                encode_frame(
                    MessageType.EVENT,
                    request_id=request_id,
                    payload=event.model_dump(mode="json"),
                )
            )
            await writer.drain()
            response = await read_frame(reader, max_frame_bytes=MAX_HOOK_RESPONSE_BYTES)
            if response is None or response.request_id != request_id:
                raise HookClientError("daemon_response_mismatch")
            if response.message_type is MessageType.ERROR:
                code = response.payload.get("code")
                if not isinstance(code, str) or not code or len(code) > 128:
                    code = "daemon_error"
                raise HookClientError(code, retryable=code in {"server_busy", "store_error"})
            if response.message_type is not MessageType.ACK:
                raise HookClientError("daemon_response_type")
            try:
                decision = PolicyDecision.model_validate(response.payload.get("decision"))
            except ValidationError as exc:
                raise HookClientError("invalid_decision") from exc
            if (
                decision.target.kind is not TargetKind.SESSION
                or decision.target.target_id != event.session.session_id
                or decision.state_version <= 0
                or not decision.state_hash
            ):
                raise HookClientError("invalid_decision")
            return decision
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
