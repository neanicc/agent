from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from loopguard_api.repair_temporal import ACTIVITIES
from loopguard_api.repair_workflow import (
    ActivityResult,
    InMemoryRepairWorkflowStore,
    RepairWorkflowService,
)


NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


class RecordingActivities:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, activity: str, *, repair_id: uuid.UUID) -> ActivityResult:
        self.calls.append(activity)
        return ActivityResult(artifact_id=f"{repair_id}:{activity}")


@pytest.mark.parametrize("boundary", ACTIVITIES)
def test_worker_restart_resumes_after_persisted_activity(boundary: str) -> None:
    """Reuse durable-looking state after a worker dies between persistence and response."""

    store = InMemoryRepairWorkflowStore()
    before_restart = RecordingActivities()
    service = RepairWorkflowService(store=store, clock=lambda: NOW)
    repair_id = service.create(
        tenant_id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        failure_fingerprint="a" * 64,
    )
    asyncio.run(service.advance(repair_id, before_restart, stop_after=boundary))

    after_restart = RecordingActivities()
    restarted = RepairWorkflowService(store=store, clock=lambda: NOW)
    recovered = asyncio.run(restarted.advance(repair_id, after_restart))

    boundary_index = ACTIVITIES.index(boundary)
    assert before_restart.calls == list(ACTIVITIES[: boundary_index + 1])
    assert after_restart.calls == list(ACTIVITIES[boundary_index + 1 :])
    assert recovered.state == "awaiting_publication"
    assert [checkpoint.activity for checkpoint in recovered.checkpoints] == list(ACTIVITIES)
