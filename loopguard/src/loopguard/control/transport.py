from __future__ import annotations

import asyncio
import inspect
import os
import socket
import stat
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol


ConnectionHandler = Callable[
    [asyncio.StreamReader, asyncio.StreamWriter],
    Awaitable[None] | None,
]


class TransportError(Exception):
    """Base class for safe local-transport failures."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TransportUnavailableError(TransportError):
    """The requested transport is not verified on this platform."""


class UnsafeTransportPathError(TransportError):
    """A socket path or its parent failed ownership/type checks."""


class LocalTransport(Protocol):
    @property
    def is_supported(self) -> bool: ...

    async def start(self, on_connection: ConnectionHandler) -> None: ...

    async def close(self) -> None: ...


class UnixSocketTransport:
    def __init__(
        self,
        socket_path: str | Path,
        *,
        peer_uid_resolver: Callable[[socket.socket], int] | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self._peer_uid_resolver = peer_uid_resolver or _peer_uid
        self._server: asyncio.AbstractServer | None = None

    @property
    def is_supported(self) -> bool:
        return os.name == "posix" and hasattr(asyncio, "start_unix_server")

    async def start(self, on_connection: ConnectionHandler) -> None:
        if not self.is_supported:
            raise TransportUnavailableError("unix_socket_unavailable")
        if self._server is not None:
            raise TransportError("transport_already_started")
        if len(os.fsencode(self.socket_path)) >= 104:
            raise UnsafeTransportPathError("socket_path_too_long")
        _prepare_socket_parent(self.socket_path.parent)
        await _remove_stale_socket(self.socket_path)

        async def authenticate(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                raw_socket = writer.get_extra_info("socket")
                if raw_socket is None or self._peer_uid_resolver(raw_socket) != os.getuid():
                    return
                result = on_connection(reader, writer)
                if inspect.isawaitable(result):
                    await result
            finally:
                if not writer.is_closing():
                    writer.close()
                try:
                    await writer.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        try:
            self._server = await asyncio.start_unix_server(
                authenticate,
                path=self.socket_path,
                limit=1_048_576 + 256,
            )
            os.chmod(self.socket_path, 0o600)
            status = os.lstat(self.socket_path)
            if not stat.S_ISSOCK(status.st_mode) or status.st_uid != os.getuid():
                raise UnsafeTransportPathError("unsafe_socket_path")
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        _unlink_owned_socket(self.socket_path)


class WindowsPipeBackend(Protocol):
    async def start(
        self,
        *,
        pipe_name: str,
        allowed_sid: str,
        on_connection: ConnectionHandler,
    ) -> None: ...

    async def close(self) -> None: ...


class WindowsNamedPipeTransport:
    """Verified-backend boundary for current-user-only Windows named pipes.

    The standard build intentionally reports this transport unavailable until a
    Windows integration supplies a backend that enforces both the pipe ACL and
    peer SID check. This prevents unsupported capability advertising.
    """

    def __init__(
        self,
        pipe_name: str,
        *,
        backend: WindowsPipeBackend | None = None,
        current_user_sid: str | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.pipe_name = pipe_name
        self._backend = backend
        self._current_user_sid = current_user_sid
        self._platform_name = platform_name or os.name

    @property
    def is_supported(self) -> bool:
        return (
            self._platform_name == "nt"
            and self._backend is not None
            and bool(self._current_user_sid)
        )

    async def start(self, on_connection: ConnectionHandler) -> None:
        if self._backend is None or self._platform_name != "nt":
            raise TransportUnavailableError("windows_named_pipe_unavailable")
        if not self._current_user_sid:
            raise TransportUnavailableError("windows_peer_identity_unavailable")
        await self._backend.start(
            pipe_name=self.pipe_name,
            allowed_sid=self._current_user_sid,
            on_connection=on_connection,
        )

    async def close(self) -> None:
        if self._backend is not None:
            await self._backend.close()


def _prepare_socket_parent(parent: Path) -> None:
    try:
        status = os.lstat(parent)
    except FileNotFoundError:
        parent.mkdir(mode=0o700, parents=True)
        status = os.lstat(parent)
    if not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode):
        raise UnsafeTransportPathError("unsafe_socket_parent")
    if status.st_uid != os.getuid():
        raise UnsafeTransportPathError("socket_parent_not_owned")
    if stat.S_IMODE(status.st_mode) != 0o700:
        raise UnsafeTransportPathError("unsafe_socket_parent_permissions")


async def _remove_stale_socket(path: Path) -> None:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(status.st_mode) or status.st_uid != os.getuid():
        raise UnsafeTransportPathError("unsafe_stale_socket")

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.setblocking(False)
    try:
        try:
            await asyncio.get_running_loop().sock_connect(probe, str(path))
        except (ConnectionRefusedError, FileNotFoundError):
            pass
        else:
            raise TransportError("socket_already_in_use")
    finally:
        probe.close()
    _unlink_owned_socket(path)


def _unlink_owned_socket(path: Path) -> None:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(status.st_mode) or status.st_uid != os.getuid():
        raise UnsafeTransportPathError("unsafe_socket_cleanup")
    path.unlink()


def _peer_uid(raw_socket: socket.socket) -> int:
    getpeereid = getattr(raw_socket, "getpeereid", None)
    if getpeereid is not None:
        uid, _gid = getpeereid()
        return int(uid)

    peer_credentials = getattr(socket, "SO_PEERCRED", None)
    if peer_credentials is not None:
        credentials = raw_socket.getsockopt(
            socket.SOL_SOCKET,
            peer_credentials,
            struct.calcsize("3i"),
        )
        _pid, uid, _gid = struct.unpack("3i", credentials)
        return int(uid)

    local_peer_credentials = getattr(socket, "LOCAL_PEERCRED", None)
    if local_peer_credentials is not None:
        credentials = raw_socket.getsockopt(0, local_peer_credentials, 12)
        _version, uid = struct.unpack_from("II", credentials)
        return int(uid)
    raise TransportUnavailableError("peer_identity_unavailable")
