from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass(frozen=True, slots=True)
class CapacityExceeded(Exception):
    resource: str
    retry_after_seconds: int

    def __str__(self) -> str:
        return f"{self.resource} capacity exceeded"


class CapacityLimiter:
    """Per-tenant bounded admission; accepted work is never silently dropped."""

    def __init__(
        self,
        *,
        actions_per_minute: int,
        active_workflows: int,
        stream_connections: int,
        retry_min_seconds: int,
        retry_max_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(
            actions_per_minute,
            active_workflows,
            stream_connections,
            retry_min_seconds,
        ) < 1 or retry_max_seconds < retry_min_seconds:
            raise ValueError("capacity limits are invalid")
        self.actions_per_minute = actions_per_minute
        self.active_workflows = active_workflows
        self.stream_connections = stream_connections
        self.retry_min_seconds = retry_min_seconds
        self.retry_max_seconds = retry_max_seconds
        self.clock = clock
        self._actions: dict[uuid.UUID, deque[float]] = defaultdict(deque)
        self._workflows: dict[uuid.UUID, set[str]] = defaultdict(set)
        self._streams: dict[uuid.UUID, int] = defaultdict(int)
        self._lock = threading.RLock()

    def admit_action(self, tenant_id: uuid.UUID) -> None:
        with self._lock:
            now = self.clock()
            events = self._actions[tenant_id]
            while events and events[0] <= now - 60:
                events.popleft()
            if len(events) >= self.actions_per_minute:
                raise self._exceeded(tenant_id, "actions")
            events.append(now)

    def admit_workflow(self, tenant_id: uuid.UUID, workflow_id: str) -> bool:
        with self._lock:
            active = self._workflows[tenant_id]
            if workflow_id in active:
                return False
            if len(active) >= self.active_workflows:
                raise self._exceeded(tenant_id, "workflows")
            active.add(workflow_id)
            return True

    def release_workflow(self, tenant_id: uuid.UUID, workflow_id: str) -> None:
        with self._lock:
            self._workflows[tenant_id].discard(workflow_id)

    @contextmanager
    def stream(self, tenant_id: uuid.UUID) -> Iterator[None]:
        with self._lock:
            if self._streams[tenant_id] >= self.stream_connections:
                raise self._exceeded(tenant_id, "streams")
            self._streams[tenant_id] += 1
        try:
            yield
        finally:
            with self._lock:
                self._streams[tenant_id] = max(0, self._streams[tenant_id] - 1)

    def _exceeded(self, tenant_id: uuid.UUID, resource: str) -> CapacityExceeded:
        width = self.retry_max_seconds - self.retry_min_seconds + 1
        digest = hashlib.sha256(f"{tenant_id}:{resource}".encode()).digest()
        jitter = int.from_bytes(digest[:4], "big") % width
        return CapacityExceeded(resource, self.retry_min_seconds + jitter)
