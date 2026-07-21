from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shutil
import signal
import struct
import subprocess
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from loopguard.control.paths import ensure_private_home


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 1_048_576


class BrowserResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    code: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default=None, max_length=1_024)
    result: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_envelope(self) -> BrowserResult:
        if self.ok and (self.code is not None or self.message is not None):
            raise ValueError("successful browser result cannot contain an error")
        if not self.ok and self.code is None:
            raise ValueError("failed browser result requires a code")
        return self


class BrokerConnection(Protocol):
    def is_alive(self) -> bool: ...

    async def request(self, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class BrokerFactory(Protocol):
    async def start(self, capability: str) -> BrokerConnection: ...


class BrowserBrokerClient:
    def __init__(
        self,
        *,
        factory: BrokerFactory,
        startup_timeout_seconds: float = 10,
        backoff_base_seconds: float = 0.1,
        backoff_max_seconds: float = 5,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if startup_timeout_seconds <= 0:
            raise ValueError("browser startup timeout must be positive")
        if backoff_base_seconds < 0 or backoff_max_seconds < backoff_base_seconds:
            raise ValueError("browser restart backoff is invalid")
        self.factory = factory
        self.startup_timeout_seconds = startup_timeout_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self._sleeper = sleeper
        self._connection: BrokerConnection | None = None
        self._connection_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._restart_failures = 0
        self._restart_required = False
        self._context_sessions: dict[str, str] = {}
        self.generation = 0
        self._closed = False

    @classmethod
    def local(cls, home: str | Path) -> BrowserBrokerClient:
        return cls(factory=LocalBrokerFactory(home=home))

    async def health(self, *, timeout_seconds: float = 5) -> BrowserResult:
        return await self._call("health", {}, timeout_seconds=timeout_seconds)

    async def create_context(
        self,
        *,
        session_id: str,
        browser: str,
        allowed_origins: Sequence[str],
        storage_state_path: str | None = None,
        locale: str = "en-US",
        timezone_id: str = "UTC",
        service_workers: str = "block",
        timeout_seconds: float = 15,
    ) -> BrowserResult:
        result = await self._call(
            "context.create",
            {
                "sessionId": session_id,
                "browser": browser,
                "storageStatePath": storage_state_path,
                "allowedOrigins": list(allowed_origins),
                "locale": locale,
                "timezoneId": timezone_id,
                "serviceWorkers": service_workers,
            },
            timeout_seconds=timeout_seconds,
        )
        context_id = result.result.get("contextId")
        if result.ok and isinstance(context_id, str):
            self._context_sessions[context_id] = session_id
        return result

    async def close_context(
        self,
        context_id: str,
        *,
        session_id: str | None = None,
        timeout_seconds: float = 5,
    ) -> BrowserResult:
        return await self._call(
            "context.close",
            {
                "contextId": context_id,
                "sessionId": session_id or self._context_sessions.get(context_id, "local"),
            },
            timeout_seconds=timeout_seconds,
        )

    async def run_page(
        self,
        context_id: str,
        *,
        session_id: str | None = None,
        actions: Sequence[Mapping[str, Any]],
        timeout_seconds: float = 30,
    ) -> BrowserResult:
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("page timeout must be between 0 and 120 seconds")
        return await self._call(
            "page.run",
            {
                "contextId": context_id,
                "sessionId": session_id or self._context_sessions.get(context_id, "local"),
                "actions": [dict(action) for action in actions],
                "timeoutMs": max(1, int(timeout_seconds * 1_000)),
            },
            timeout_seconds=timeout_seconds,
        )

    async def close(self) -> None:
        self._closed = True
        async with self._connection_lock:
            connection = self._connection
            self._connection = None
        if connection is not None:
            await connection.close()

    async def _call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> BrowserResult:
        if timeout_seconds <= 0:
            raise ValueError("browser request timeout must be positive")
        async with self._request_lock:
            try:
                connection = await self._ensure_connection()
            except Exception:
                return BrowserResult(
                    ok=False,
                    code="broker_unavailable",
                    message="Browser broker is unavailable.",
                )
            request_id = f"request-{uuid.uuid4().hex}"
            body = {"id": request_id, "method": method, "params": params}
            try:
                raw = await asyncio.wait_for(
                    connection.request(body, timeout_seconds),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                await self._invalidate(connection)
                return BrowserResult(
                    ok=False,
                    code="timeout",
                    message="Browser command reached its hard timeout.",
                )
            except Exception:
                await self._invalidate(connection)
                return BrowserResult(
                    ok=False,
                    code="transport_error",
                    message="Browser broker communication failed.",
                )
            try:
                return _parse_response(raw, request_id)
            except ValueError:
                await self._invalidate(connection)
                return BrowserResult(
                    ok=False,
                    code="protocol_error",
                    message="Browser broker returned an invalid response.",
                )

    async def _ensure_connection(self) -> BrokerConnection:
        if self._closed:
            raise RuntimeError("browser broker client is closed")
        async with self._connection_lock:
            current = self._connection
            if current is not None and current.is_alive():
                return current
            if current is not None:
                await current.close()
                self._connection = None
                self._restart_required = True
            if self._restart_required:
                delay = min(
                    self.backoff_max_seconds,
                    self.backoff_base_seconds * (2**self._restart_failures),
                )
                self._restart_failures += 1
                if delay:
                    await self._sleeper(delay)
            capability = secrets.token_urlsafe(48)
            try:
                connection = await self.factory.start(capability)
            except Exception:
                self._restart_required = True
                raise
            try:
                handshake = await asyncio.wait_for(
                    connection.request(
                        {
                            "type": "hello",
                            "protocolVersion": PROTOCOL_VERSION,
                            "capability": capability,
                        },
                        self.startup_timeout_seconds,
                    ),
                    timeout=self.startup_timeout_seconds,
                )
                _parse_handshake(handshake)
            except Exception:
                await connection.close()
                self._restart_required = True
                raise
            self._connection = connection
            self.generation += 1
            self._restart_failures = 0
            self._restart_required = False
            return connection

    async def _invalidate(self, connection: BrokerConnection) -> None:
        async with self._connection_lock:
            if self._connection is connection:
                self._connection = None
                self._restart_required = True
        await connection.close()


class LocalBrokerFactory:
    def __init__(
        self,
        *,
        home: str | Path,
        command: Sequence[str] | None = None,
        startup_timeout_seconds: float = 10,
    ) -> None:
        self.home = Path(home).expanduser().absolute()
        ensure_private_home(self.home)
        self.state_directory = self.home / "browser"
        ensure_private_home(self.state_directory)
        self.startup_timeout_seconds = startup_timeout_seconds
        self.command = tuple(command) if command is not None else None
        if self.command is not None and (
            not self.command or any(not isinstance(item, str) or not item for item in self.command)
        ):
            raise ValueError("browser broker command must be a non-empty argv array")

    async def start(self, capability: str) -> BrokerConnection:
        endpoint = _endpoint(self.state_directory)
        environment = dict(os.environ)
        environment["LOOPGUARD_BROWSER_CAPABILITY"] = capability
        kwargs: dict[str, Any] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        command = self.command or _default_broker_command()
        process = await asyncio.create_subprocess_exec(
            *command,
            "--socket",
            endpoint,
            "--state-directory",
            str(self.state_directory),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
            **kwargs,
        )
        deadline = asyncio.get_running_loop().time() + self.startup_timeout_seconds
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                break
            try:
                reader, writer = await _open_endpoint(endpoint)
                return FramedBrokerConnection(
                    reader=reader,
                    writer=writer,
                    process=process,
                )
            except (ConnectionError, FileNotFoundError, OSError) as exc:
                last_error = exc
                await asyncio.sleep(0.025)
        await _terminate_process(process)
        raise RuntimeError("browser broker did not become ready") from last_error


class FramedBrokerConnection:
    def __init__(
        self,
        *,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        process: asyncio.subprocess.Process,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.process = process
        self._closed = False

    def is_alive(self) -> bool:
        return not self._closed and self.process.returncode is None and not self.writer.is_closing()

    async def request(self, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        payload = json.dumps(
            body,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if not payload or len(payload) > MAX_FRAME_BYTES:
            raise ValueError("browser request frame exceeds the size limit")
        self.writer.write(struct.pack(">I", len(payload)) + payload)
        await asyncio.wait_for(self.writer.drain(), timeout=timeout_seconds)
        header = await asyncio.wait_for(self.reader.readexactly(4), timeout=timeout_seconds)
        (length,) = struct.unpack(">I", header)
        if length <= 0 or length > MAX_FRAME_BYTES:
            raise ValueError("browser response frame length is invalid")
        raw = await asyncio.wait_for(self.reader.readexactly(length), timeout=timeout_seconds)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("browser response frame is malformed") from exc
        if not isinstance(value, dict):
            raise ValueError("browser response must be an object")
        return value

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()
        await _terminate_process(self.process)


def _parse_handshake(value: Mapping[str, Any]) -> None:
    if set(value) != {"id", "ok", "result"}:
        raise ValueError("invalid broker handshake envelope")
    result = value.get("result")
    if (
        value.get("id") != "handshake"
        or value.get("ok") is not True
        or not isinstance(result, dict)
        or set(result) != {"protocolVersion"}
        or result.get("protocolVersion") != PROTOCOL_VERSION
    ):
        raise ValueError("invalid browser broker handshake")


def _parse_response(value: Mapping[str, Any], request_id: str) -> BrowserResult:
    if value.get("id") != request_id or not isinstance(value.get("ok"), bool):
        raise ValueError("browser response correlation failed")
    if value["ok"] is True:
        if set(value) != {"id", "ok", "result"} or not isinstance(value["result"], dict):
            raise ValueError("browser success response is invalid")
        return BrowserResult(ok=True, result=value["result"])
    if set(value) != {"id", "ok", "error"} or not isinstance(value["error"], dict):
        raise ValueError("browser error response is invalid")
    error = value["error"]
    if set(error) != {"code", "message"}:
        raise ValueError("browser error body is invalid")
    code = error.get("code")
    message = error.get("message")
    if (
        not isinstance(code, str)
        or not code
        or len(code) > 64
        or not isinstance(message, str)
        or not message
        or len(message) > 1_024
        or "\x00" in code + message
    ):
        raise ValueError("browser error body is invalid")
    return BrowserResult(ok=False, code=code, message=message)


def _default_broker_command() -> tuple[str, ...]:
    node = shutil.which("node")
    root = Path(__file__).resolve().parents[3] / "integrations" / "browser-broker"
    tsx = root / "node_modules" / "tsx" / "dist" / "cli.mjs"
    server = root / "src" / "server.ts"
    if node is None or not tsx.is_file() or not server.is_file():
        raise RuntimeError("pinned browser broker runtime is not installed")
    return node, str(tsx), str(server), "--"


def _endpoint(state_directory: Path) -> str:
    token = secrets.token_hex(4)
    if os.name == "nt":
        return rf"\\.\pipe\loopguard-browser-{token}"
    return str(state_directory / f"b-{token}.sock")


async def _open_endpoint(endpoint: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if os.name != "nt":
        return await asyncio.open_unix_connection(endpoint)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    create_pipe = getattr(loop, "create_pipe_connection", None)
    if create_pipe is None:
        raise RuntimeError("Windows event loop does not support named-pipe clients")
    transport, _ = await create_pipe(lambda: protocol, endpoint)
    return reader, asyncio.StreamWriter(transport, protocol, reader, loop)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
