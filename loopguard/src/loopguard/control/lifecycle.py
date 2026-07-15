from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable

from loopguard.configuration import ControlConfiguration
from loopguard.context.capability import ContextMCPLauncher
from loopguard.context.digest import DigestBuilder
from loopguard.context.handoff import HandoffStore
from loopguard.context.index import SymbolIndex
from loopguard.context.journal import ChangeJournal
from loopguard.context.leases import LeaseManager
from loopguard.context.watcher import ChangeReconciler
from loopguard.context.worktrees import WorktreeManager

from .daemon import DaemonServices, LoopGuardDaemon
from .dispatch import Handler
from .paths import (
    ControlPaths,
    ensure_private_home,
    remove_owned_pid_file,
    write_pid_file,
)
from .store import EventStore


def production_handlers(journal: ChangeJournal) -> dict[str, Handler]:
    return {"context": journal.handle_delivery}


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
    journal: ChangeJournal | None = None
    symbol_index: SymbolIndex | None = None
    lease_manager: LeaseManager | None = None
    handoff_store: HandoffStore | None = None
    mcp_launcher: ContextMCPLauncher | None = None
    worktree_manager: WorktreeManager | None = None
    daemon: LoopGuardDaemon | None = None
    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    try:
        store = store_factory() if store_factory is not None else EventStore.open(home=paths.home)
        journal = ChangeJournal(paths.home / "context.db")
        symbol_index = SymbolIndex(paths.home / "symbols.db")
        lease_manager = LeaseManager(paths.home / "leases.db")
        handoff_store = HandoffStore(paths.home / "handoffs.db")
        mcp_launcher = ContextMCPLauncher(paths.home)
        worktree_manager = WorktreeManager(root=paths.home / "worktrees")
        services = DaemonServices(
            change_journal=journal,
            change_watcher=ChangeReconciler,
            symbol_index=symbol_index,
            lease_manager=lease_manager,
            digest_service=DigestBuilder(),
            handoff_store=handoff_store,
            worktree_manager=worktree_manager,
            context_mcp_launcher=mcp_launcher,
            attached_collision_policy=configuration.daemon.attached_collision_policy,
        )
        settings = configuration.daemon
        daemon = LoopGuardDaemon(
            store=store,
            socket_path=paths.socket,
            handlers=production_handlers(journal),
            services=services,
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
        if mcp_launcher is not None:
            mcp_launcher.close()
        if worktree_manager is not None:
            worktree_manager.close()
        if handoff_store is not None:
            handoff_store.close()
        if lease_manager is not None:
            lease_manager.close()
        if symbol_index is not None:
            symbol_index.close()
        if journal is not None:
            journal.close()
        if store is not None:
            store.close()
        remove_owned_pid_file(paths.pid)
