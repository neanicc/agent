from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable

from loopguard.configuration import ControlConfiguration

from .daemon import LoopGuardDaemon
from .paths import (
    ControlPaths,
    ensure_private_home,
    remove_owned_pid_file,
    write_pid_file,
)
from .store import EventStore


async def run_foreground_daemon(
    paths: ControlPaths,
    configuration: ControlConfiguration,
    *,
    on_ready: Callable[[], None] | None = None,
    stop_event: asyncio.Event | None = None,
    store_factory: Callable[[], EventStore] | None = None,
) -> None:
    ensure_private_home(paths.home)
    write_pid_file(paths.pid, os.getpid())
    store: EventStore | None = None
    daemon: LoopGuardDaemon | None = None
    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    try:
        store = store_factory() if store_factory is not None else EventStore.open(home=paths.home)
        settings = configuration.daemon
        daemon = LoopGuardDaemon(
            store=store,
            socket_path=paths.socket,
            max_frame_bytes=settings.max_frame_bytes,
            max_concurrent_clients=settings.max_concurrent_clients,
            idle_timeout=settings.idle_timeout_seconds,
            write_timeout=settings.write_timeout_seconds,
            handler_queue_size=settings.handler_queue_size,
            handler_max_attempts=settings.handler_max_attempts,
            handler_retry_delay=settings.handler_retry_delay_seconds,
        )
        await daemon.start()
        for candidate in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(candidate, stop.set)
            except (NotImplementedError, RuntimeError):
                continue
            installed_signals.append(candidate)
        if on_ready is not None:
            on_ready()
        await stop.wait()
    finally:
        for candidate in installed_signals:
            loop.remove_signal_handler(candidate)
        if daemon is not None:
            await daemon.close()
        if store is not None:
            store.close()
        remove_owned_pid_file(paths.pid)
