from __future__ import annotations

import asyncio
import os
import stat

import pytest

from loopguard.control.transport import (
    TransportUnavailableError,
    UnsafeTransportPathError,
    UnixSocketTransport,
    WindowsNamedPipeTransport,
)

from .daemon_test_support import short_socket_path


class _FakeWindowsPipeBackend:
    def __init__(self, *, peer_sid: str = "SID-current") -> None:
        self.peer_sid = peer_sid
        self.started_with: dict[str, object] | None = None
        self.closed = False

    async def start(self, *, pipe_name, allowed_sid, on_connection):
        self.started_with = {
            "pipe_name": pipe_name,
            "allowed_sid": allowed_sid,
            "on_connection": on_connection,
        }

    async def close(self):
        self.closed = True


def test_windows_transport_is_not_advertised_without_verified_backend():
    transport = WindowsNamedPipeTransport(r"\\.\pipe\loopguard")

    assert transport.is_supported is False
    with pytest.raises(TransportUnavailableError) as raised:
        asyncio.run(transport.start(lambda _reader, _writer: None))
    assert raised.value.code == "windows_named_pipe_unavailable"


def test_injected_windows_backend_receives_current_user_only_acl_and_closes():
    async def scenario():
        backend = _FakeWindowsPipeBackend()
        transport = WindowsNamedPipeTransport(
            r"\\.\pipe\loopguard",
            backend=backend,
            current_user_sid="SID-current",
            platform_name="nt",
        )

        await transport.start(lambda _reader, _writer: None)
        assert transport.is_supported is True
        assert backend.started_with is not None
        assert backend.started_with["allowed_sid"] == "SID-current"
        await transport.close()
        assert backend.closed is True

    asyncio.run(scenario())


def test_windows_transport_rejects_backend_without_current_user_sid():
    transport = WindowsNamedPipeTransport(
        r"\\.\pipe\loopguard",
        backend=_FakeWindowsPipeBackend(),
        current_user_sid=None,
        platform_name="nt",
    )

    with pytest.raises(TransportUnavailableError) as raised:
        asyncio.run(transport.start(lambda _reader, _writer: None))
    assert raised.value.code == "windows_peer_identity_unavailable"


@pytest.mark.skipif(os.name != "posix", reason="Unix socket transport is POSIX-only")
def test_unix_transport_rejects_insecure_existing_parent_without_chmod():
    async def scenario():
        with short_socket_path() as socket_path:
            os.chmod(socket_path.parent, 0o755)
            transport = UnixSocketTransport(socket_path)
            with pytest.raises(UnsafeTransportPathError) as raised:
                await transport.start(lambda _reader, _writer: None)
            assert raised.value.code == "unsafe_socket_parent_permissions"
            assert socket_path.parent.stat().st_mode & 0o777 == 0o755

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="Unix socket transport is POSIX-only")
def test_unix_transport_rejects_overlong_path_with_stable_code(tmp_path):
    socket_path = tmp_path / ("x" * 120)
    transport = UnixSocketTransport(socket_path)

    with pytest.raises(UnsafeTransportPathError) as raised:
        asyncio.run(transport.start(lambda _reader, _writer: None))

    assert raised.value.code == "socket_path_too_long"


@pytest.mark.skipif(os.name != "posix", reason="Unix socket transport is POSIX-only")
def test_unix_socket_is_owner_only_and_removed_on_close():
    async def scenario():
        with short_socket_path() as socket_path:
            transport = UnixSocketTransport(socket_path)
            await transport.start(lambda _reader, _writer: None)
            assert stat.S_IMODE(os.lstat(socket_path).st_mode) == 0o600
            await transport.close()
            assert not socket_path.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="Unix socket transport is POSIX-only")
def test_unix_transport_never_unlinks_a_non_socket_stale_path():
    async def scenario():
        with short_socket_path() as socket_path:
            socket_path.write_text("keep")
            transport = UnixSocketTransport(socket_path)
            with pytest.raises(UnsafeTransportPathError) as raised:
                await transport.start(lambda _reader, _writer: None)
            assert raised.value.code == "unsafe_stale_socket"
            assert socket_path.read_text() == "keep"

    asyncio.run(scenario())
