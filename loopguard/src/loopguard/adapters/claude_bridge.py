from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from loopguard.adapters.jsonrpc import JsonObject, JsonRpcClient


BridgeMessageHandler = Callable[[JsonObject], Awaitable[None]]


class ClaudeBridgeClient:
    """Bounded process client for LoopGuard's pinned Claude Agent SDK bridge."""

    def __init__(self, rpc: JsonRpcClient) -> None:
        self._rpc = rpc
        self._handler: BridgeMessageHandler | None = None

    @classmethod
    async def launch(cls, handler: BridgeMessageHandler) -> ClaudeBridgeClient:
        root = Path(__file__).resolve().parents[3]
        integration = root / "integrations" / "claude-bridge"
        executable_name = "tsx.cmd" if os.name == "nt" else "tsx"
        executable = integration / "node_modules" / ".bin" / executable_name
        entrypoint = integration / "src" / "index.ts"
        if not executable.is_file() or not entrypoint.is_file():
            raise FileNotFoundError(
                "Claude bridge is not installed; run npm ci in integrations/claude-bridge"
            )
        client: ClaudeBridgeClient | None = None

        async def dispatch(message: JsonObject) -> None:
            if client is None or client._handler is None:
                raise RuntimeError("Claude bridge emitted before its handler was installed")
            await client._handler(message)

        rpc = await JsonRpcClient.launch(
            (str(executable), str(entrypoint)),
            on_message=dispatch,
            max_frame_bytes=1_048_576,
            max_pending=128,
        )
        client = cls(rpc)
        client.set_message_handler(handler)
        return client

    def set_message_handler(self, handler: BridgeMessageHandler) -> None:
        self._handler = handler

    async def request(self, method: str, params: Mapping[str, Any], *, timeout: float) -> Any:
        return await self._rpc.request(method, params, timeout=timeout)

    async def close(self) -> None:
        await self._rpc.close()

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self._rpc.diagnostics
