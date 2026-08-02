from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any


JsonObject = dict[str, Any]
MessageHandler = Callable[[JsonObject], Awaitable[None]]
_SECRET = re.compile(
    r"(?i)(authorization|api[_-]?key|token|secret|password)"
    r"(\s*[:=]\s*)((?:bearer\s+)?[^\s,;]+)"
)


class JsonRpcError(RuntimeError):
    """Base failure for a managed JSON-RPC process."""


class JsonRpcProtocolError(JsonRpcError):
    """The peer violated the bounded JSON-RPC transport contract."""


class JsonRpcRemoteError(JsonRpcError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"JSON-RPC peer returned {code}: {message}")
        self.code = code
        self.remote_message = message
        self.data = data


class JsonRpcProcessExited(JsonRpcError):
    """The managed child exited before completing an operation."""


class JsonRpcTimeout(JsonRpcError):
    """A JSON-RPC operation exceeded its independent timeout."""


def decode_jsonrpc_line(line: bytes, *, max_frame_bytes: int = 1_048_576) -> JsonObject:
    if max_frame_bytes < 256:
        if len(line) > max_frame_bytes:
            raise JsonRpcProtocolError("JSON-RPC frame exceeds the maximum size")
    elif len(line) > max_frame_bytes:
        raise JsonRpcProtocolError("JSON-RPC frame exceeds the maximum size")
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JsonRpcProtocolError("JSON-RPC frame is not valid JSON") from exc
    if not isinstance(value, dict):
        raise JsonRpcProtocolError("JSON-RPC frame must be an object")
    method = value.get("method")
    has_response = "result" in value or "error" in value
    if method is None and not has_response:
        raise JsonRpcProtocolError("JSON-RPC frame is missing method or correlation result")
    if method is not None and (not isinstance(method, str) or not method):
        raise JsonRpcProtocolError("JSON-RPC method must be a non-empty string")
    if has_response and "id" not in value:
        raise JsonRpcProtocolError("JSON-RPC response is missing its correlation id")
    if "result" in value and "error" in value:
        raise JsonRpcProtocolError("JSON-RPC response cannot contain result and error")
    return value


class JsonRpcClient:
    """Bounded JSONL JSON-RPC client for a single stdio child process."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        on_message: MessageHandler,
        max_frame_bytes: int = 1_048_576,
        max_pending: int = 128,
        max_diagnostics: int = 100,
    ) -> None:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ValueError("JSON-RPC child requires piped stdin, stdout, and stderr")
        if not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096")
        self.process = process
        self._stdin = process.stdin
        self._stdout = process.stdout
        self._stderr = process.stderr
        self._on_message = on_message
        self._max_frame_bytes = max_frame_bytes
        self._max_pending = max_pending
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._fatal: BaseException | None = None
        self._diagnostics: deque[str] = deque(maxlen=max_diagnostics)
        self._reader_task = asyncio.create_task(self._read_stdout(), name="codex-jsonrpc-stdout")
        self._stderr_task = asyncio.create_task(self._read_stderr(), name="codex-jsonrpc-stderr")

    @classmethod
    async def launch(
        cls,
        command: Sequence[str],
        *,
        on_message: MessageHandler,
        max_frame_bytes: int = 1_048_576,
        max_pending: int = 128,
    ) -> JsonRpcClient:
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("JSON-RPC command must contain bounded non-empty arguments")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
            limit=max_frame_bytes + 1,
        )
        return cls(
            process,
            on_message=on_message,
            max_frame_bytes=max_frame_bytes,
            max_pending=max_pending,
        )

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    async def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout: float,
    ) -> Any:
        self._require_available()
        if len(self._pending) >= self._max_pending:
            raise JsonRpcProtocolError("JSON-RPC pending request limit reached")
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": dict(params)})
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
            except TimeoutError as exc:
                future.cancel()
                failure = JsonRpcTimeout(f"JSON-RPC method {method!r} timed out")
                self._fatal = failure
                await _terminate_process_tree(self.process)
                raise failure from exc
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._require_available()
        await self._write({"method": method, "params": dict(params)})

    async def respond(self, request_id: str | int, result: Mapping[str, Any]) -> None:
        self._require_available()
        await self._write({"id": request_id, "result": dict(result)})

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        error = JsonRpcProcessExited("managed JSON-RPC process was closed")
        self._fail_pending(error)
        if self.process.returncode is None:
            await _terminate_process_tree(self.process)
        current = asyncio.current_task()
        for task in (self._reader_task, self._stderr_task):
            if task is not current and not task.done():
                task.cancel()
        await asyncio.gather(self._reader_task, self._stderr_task, return_exceptions=True)

    async def _write(self, message: JsonObject) -> None:
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
        if len(encoded) > self._max_frame_bytes:
            raise JsonRpcProtocolError("JSON-RPC frame exceeds the maximum size")
        async with self._write_lock:
            self._require_available()
            self._stdin.write(encoded)
            try:
                await self._stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise JsonRpcProcessExited("managed JSON-RPC process closed stdin") from exc

    async def _read_stdout(self) -> None:
        try:
            while True:
                line = await self._stdout.readline()
                if not line:
                    raise JsonRpcProcessExited(
                        f"managed JSON-RPC process exited with code {self.process.returncode}"
                    )
                message = decode_jsonrpc_line(line, max_frame_bytes=self._max_frame_bytes)
                if "method" in message:
                    await self._on_message(message)
                    continue
                request_id = message["id"]
                if not isinstance(request_id, int) or request_id not in self._pending:
                    raise JsonRpcProtocolError("JSON-RPC response has an unknown request id")
                future = self._pending[request_id]
                if "error" in message:
                    error = message["error"]
                    if not isinstance(error, dict):
                        raise JsonRpcProtocolError("JSON-RPC error payload must be an object")
                    future.set_exception(
                        JsonRpcRemoteError(
                            int(error.get("code", -32000)),
                            str(error.get("message", "unknown remote error"))[:512],
                            error.get("data"),
                        )
                    )
                else:
                    future.set_result(message.get("result"))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._fatal = exc
            self._fail_pending(exc)
            if not self._closed and self.process.returncode is None:
                await _terminate_process_tree(self.process)

    async def _read_stderr(self) -> None:
        try:
            while True:
                line = await self._stderr.readline()
                if not line:
                    return
                self._diagnostics.append(_redact_diagnostic(line.decode(errors="replace")))
        except asyncio.CancelledError:
            raise

    def _require_available(self) -> None:
        if self._fatal is not None:
            if isinstance(self._fatal, JsonRpcError):
                raise self._fatal
            raise JsonRpcProcessExited("managed JSON-RPC reader failed") from self._fatal
        if self._closed or self.process.returncode is not None:
            raise JsonRpcProcessExited("managed JSON-RPC process is not running")

    def _fail_pending(self, error: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)


def _redact_diagnostic(value: str) -> str:
    bounded = value.strip()[:2_048]
    return _SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", bounded)


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            process.terminate()
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
        return
    except TimeoutError:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            process.kill()
        except ProcessLookupError:
            return
    await process.wait()
