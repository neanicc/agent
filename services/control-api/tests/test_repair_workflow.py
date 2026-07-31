from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import workflow
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from loopguard_api.authorization import Permission, Principal
from loopguard_api.models import Repair, RepairCandidate
from loopguard_api.repair_temporal import TemporalRepairWorkflow
from loopguard_api.repair_workflow import (
    ACTIVITIES,
    ActivityResult,
    RepairWorkflowRecord,
    RepairWorkflowService,
)


TENANT = uuid.uuid4()
REPOSITORY = uuid.uuid4()
NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


class Activities:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, activity: str, *, repair_id: uuid.UUID) -> ActivityResult:
        self.calls.append(activity)
        payload = {}
        if activity == "reproduce":
            payload = {"status": "reproduced", "attempts": 1}
        if activity == "generate_candidates":
            payload = {
                "candidates": [
                    {
                        "id": "candidate-1",
                        "strategy": "normalize_ingestion_boundary",
                        "changed_files": ["src/ingest.py"],
                        "changed_lines": 8,
                        "patch_artifact_id": "artifact-patch",
                    }
                ]
            }
        if activity == "rank":
            payload = {
                "winning_candidate_id": "candidate-1",
                "reason": "smallest fully verified compatible patch",
            }
        return ActivityResult(
            artifact_id=f"artifact-{activity}",
            payload=payload,
            cost_usd="0.01",
        )


class WorkflowController:
    def __init__(self) -> None:
        self.started: list[uuid.UUID] = []
        self.signals: list[tuple[str, uuid.UUID]] = []

    async def start(self, record: RepairWorkflowRecord) -> None:
        if record.id not in self.started:
            self.started.append(record.id)

    async def authorize_publication(self, repair_id: uuid.UUID, **_values: object) -> None:
        self.signals.append(("publish", repair_id))

    async def cancel(self, repair_id: uuid.UUID) -> None:
        self.signals.append(("cancel", repair_id))

    async def retry(self, record: RepairWorkflowRecord) -> None:
        self.signals.append(("retry", record.id))


def _principal(permission: Permission) -> Principal:
    return Principal(
        TENANT,
        uuid.uuid4(),
        "subject",
        "admin",
        frozenset({Permission.VIEW_SESSION, permission}),
    )


def test_workflow_resumes_after_worker_restart_without_repeating_activity() -> None:
    service = RepairWorkflowService(clock=lambda: NOW)
    repair_id = service.create(
        tenant_id=TENANT,
        repository_id=REPOSITORY,
        failure_fingerprint="f" * 64,
    )
    activities = Activities()

    asyncio.run(service.advance(repair_id, activities, stop_after="reproduce"))
    restarted = RepairWorkflowService(store=service.store, clock=lambda: NOW)
    result = asyncio.run(restarted.advance(repair_id, activities))

    assert result.state == "awaiting_publication"
    assert activities.calls.count("reproduce") == 1
    assert result.reproduction["attempts"] == 1
    assert all(checkpoint.artifact_id for checkpoint in result.checkpoints)


@pytest.mark.parametrize("boundary", ACTIVITIES)
def test_worker_restart_replays_every_activity_boundary_exactly_once(boundary: str) -> None:
    service = RepairWorkflowService(clock=lambda: NOW)
    repair_id = service.create(
        tenant_id=TENANT,
        repository_id=REPOSITORY,
        failure_fingerprint="f" * 64,
    )
    activities = Activities()

    asyncio.run(service.advance(repair_id, activities, stop_after=boundary))
    restarted = RepairWorkflowService(store=service.store, clock=lambda: NOW)
    result = asyncio.run(restarted.advance(repair_id, activities))

    assert result.state == "awaiting_publication"
    assert activities.calls == list(ACTIVITIES)
    assert [checkpoint.activity for checkpoint in result.checkpoints] == list(ACTIVITIES)


def test_intake_starts_one_opaque_workflow_for_duplicate_delivery() -> None:
    controller = WorkflowController()
    service = RepairWorkflowService(
        clock=lambda: NOW,
        workflow_controller=controller,
        workflow_registered=True,
    )
    repair_id = uuid.uuid4()

    for _ in range(2):
        asyncio.run(
            service.accept_intake(
                repair_id=repair_id,
                tenant_id=TENANT,
                repository_handle="rh_coordinates",
                failure_fingerprint="f" * 64,
            )
        )

    assert controller.started == [repair_id]


def test_publication_waits_for_authorized_unexpired_expected_state() -> None:
    service = RepairWorkflowService(clock=lambda: NOW)
    repair_id = service.create(
        tenant_id=TENANT,
        repository_id=REPOSITORY,
        failure_fingerprint="f" * 64,
    )
    activities = Activities()
    waiting = asyncio.run(service.advance(repair_id, activities))

    denied = asyncio.run(
        service.signal_publish(
            repair_id,
            principal=_principal(Permission.RUN_REPAIR),
            expected_state_version=waiting.state_version,
            expected_state_hash=waiting.state_hash,
            action_id="action-viewer",
        )
    )
    assert denied is False
    assert service.read(TENANT, repair_id).state == "awaiting_publication"

    accepted = asyncio.run(
        service.signal_publish(
            repair_id,
            principal=_principal(Permission.PUBLISH_REPAIR),
            expected_state_version=waiting.state_version,
            expected_state_hash=waiting.state_hash,
            action_id="action-publisher",
        )
    )
    assert accepted is True
    completed = asyncio.run(service.advance(repair_id, activities))
    assert completed.state == "completed"
    assert activities.calls.count("publish") == 1
    assert activities.calls.count("render_report") == 1


def test_publication_deadline_is_finite_and_releases_resources() -> None:
    now = [NOW]
    service = RepairWorkflowService(clock=lambda: now[0])
    repair_id = service.create(
        tenant_id=TENANT,
        repository_id=REPOSITORY,
        failure_fingerprint="f" * 64,
        publication_timeout=timedelta(days=1),
    )
    activities = Activities()
    asyncio.run(service.advance(repair_id, activities))

    now[0] += timedelta(days=2)
    expired = asyncio.run(service.advance(repair_id, activities))

    assert expired.state == "cancelled"
    assert expired.cancellation_reason == "publication_approval_expired"
    assert activities.calls[-1] == "release_resources"


def test_activity_budget_and_cancellation_are_fail_closed() -> None:
    service = RepairWorkflowService(clock=lambda: NOW, maximum_cost_usd="0.03")
    repair_id = service.create(
        tenant_id=TENANT,
        repository_id=REPOSITORY,
        failure_fingerprint="f" * 64,
    )
    result = asyncio.run(service.advance(repair_id, Activities()))

    assert result.state == "failed"
    assert result.cancellation_reason == "workflow_cost_budget_exceeded"
    assert "release_resources" in result.checkpoints[-1].activity


def test_cancel_and_retry_are_authorized_versioned_and_idempotent() -> None:
    controller = WorkflowController()
    service = RepairWorkflowService(
        clock=lambda: NOW,
        workflow_controller=controller,
        workflow_registered=True,
    )
    repair_id = service.create(
        tenant_id=TENANT,
        repository_id=REPOSITORY,
        failure_fingerprint="f" * 64,
    )
    activities = Activities()
    waiting = asyncio.run(service.advance(repair_id, activities))

    assert asyncio.run(
        service.cancel(
            repair_id,
            principal=_principal(Permission.RUN_REPAIR),
            expected_state_version=waiting.state_version,
            expected_state_hash=waiting.state_hash,
            action_id="action-cancel",
        )
    )
    cancelled = service.read(TENANT, repair_id)
    assert cancelled is not None
    assert cancelled.state == "cancelled"
    assert not asyncio.run(
        service.retry(
            repair_id,
            principal=_principal(Permission.RUN_REPAIR),
            expected_state_version=waiting.state_version,
            expected_state_hash=waiting.state_hash,
            action_id="action-stale",
        )
    )
    assert asyncio.run(
        service.retry(
            repair_id,
            principal=_principal(Permission.RUN_REPAIR),
            expected_state_version=cancelled.state_version,
            expected_state_hash=cancelled.state_hash,
            action_id="action-retry",
        )
    )
    retried = asyncio.run(service.advance(repair_id, activities))

    assert retried.state == "awaiting_publication"
    assert activities.calls == list(ACTIVITIES)
    assert controller.signals == [
        ("cancel", repair_id),
        ("retry", repair_id),
    ]


def test_existing_repair_schema_is_extended_in_place() -> None:
    assert {
        "state_version",
        "state_hash",
        "workflow_state",
        "ranking",
        "publication",
        "total_cost",
    } <= set(Repair.__table__.columns.keys())
    assert {
        "strategy",
        "changed_files",
        "changed_lines",
        "evaluation",
    } <= set(RepairCandidate.__table__.columns.keys())


def test_temporal_workflow_imports_inside_deterministic_sandbox() -> None:
    async def prepare() -> None:
        definition = workflow._Definition.must_from_class(TemporalRepairWorkflow)
        SandboxedWorkflowRunner().prepare_workflow(definition)

    asyncio.run(prepare())
