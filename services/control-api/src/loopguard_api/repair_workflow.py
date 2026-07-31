from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
    WorkflowIDReusePolicy,
)

from .authorization import Permission, Principal
from .repair_temporal import ACTIVITIES, TemporalRepairWorkflow, activity_state


_MAX_PUBLICATION_TIMEOUT = timedelta(days=30)
_STATE_ORDER = {
    "intake": 0,
    "building_fixture": 1,
    "reproducing": 2,
    "planning": 3,
    "generating": 4,
    "evaluating": 5,
    "ranking": 6,
    "rendering_report": 7,
    "awaiting_publication": 8,
    "completed": 9,
}


class WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActivityResult(WorkflowModel):
    artifact_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0, le=10_000)


class ActivityCheckpoint(WorkflowModel):
    activity: str = Field(min_length=1, max_length=128)
    artifact_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    cost_usd: Decimal = Field(ge=0, le=10_000)
    completed_at: datetime


class RepairWorkflowRecord(WorkflowModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    repository_id: uuid.UUID
    failure_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: str
    state_version: int = Field(ge=0)
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    updated_at: datetime
    publication_deadline: datetime
    approved_action_id: str | None = None
    cancellation_reason: str | None = None
    total_cost_usd: Decimal = Field(ge=0)
    checkpoints: tuple[ActivityCheckpoint, ...] = ()
    reproduction: dict[str, Any] = Field(default_factory=dict)
    candidates: tuple[dict[str, Any], ...] = ()
    ranking: dict[str, Any] = Field(default_factory=dict)
    rollback: str = "Revert the repair commit and rerun the original pipeline."
    publication: dict[str, Any] = Field(default_factory=lambda: {"status": "not_started"})


class RepairSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    repository_id: uuid.UUID
    state: str
    failure_fingerprint: str
    created_at: datetime
    updated_at: datetime
    winning_candidate_id: str | None = None


class RepairDetail(RepairSummary):
    state_version: int
    state_hash: str
    reproduction: dict[str, Any]
    candidates: list[dict[str, Any]]
    ranking: dict[str, Any]
    rollback: str
    publication: dict[str, Any]
    capability: dict[str, Any]


class RepairPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RepairSummary]
    next_cursor: str | None
    capability: dict[str, Any]


class RepairActivities(Protocol):
    async def run(self, activity: str, *, repair_id: uuid.UUID) -> ActivityResult: ...


class RepairWorkflowController(Protocol):
    async def start(self, record: RepairWorkflowRecord) -> None: ...

    async def authorize_publication(
        self,
        repair_id: uuid.UUID,
        *,
        action_id: str,
        expected_state_version: int,
        expected_state_hash: str,
    ) -> None: ...

    async def cancel(self, repair_id: uuid.UUID) -> None: ...

    async def retry(self, record: RepairWorkflowRecord) -> None: ...


class InMemoryRepairWorkflowStore:
    """Deterministic test adapter; hosted deployments inject a durable projection."""

    durable = False

    def __init__(self) -> None:
        self.records: dict[uuid.UUID, RepairWorkflowRecord] = {}
        self.checkpoints: dict[tuple[uuid.UUID, str], ActivityCheckpoint] = {}
        self.lock = threading.RLock()


class RepairWorkflowService:
    def __init__(
        self,
        *,
        store: InMemoryRepairWorkflowStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cursor_secret: bytes = b"loopguard-test-repair-cursor-secret",
        maximum_cost_usd: Decimal | str = Decimal("25"),
        workflow_controller: RepairWorkflowController | None = None,
        workflow_registered: bool = False,
    ) -> None:
        if len(cursor_secret) < 16:
            raise ValueError("repair cursor secret must contain at least 16 bytes")
        self.store = store or InMemoryRepairWorkflowStore()
        self.clock = clock
        self.cursor_secret = cursor_secret
        self.maximum_cost_usd = Decimal(maximum_cost_usd)
        if not self.maximum_cost_usd.is_finite() or self.maximum_cost_usd <= 0:
            raise ValueError("repair workflow cost budget must be finite and positive")
        self.workflow_controller = workflow_controller
        self.workflow_registered = workflow_registered

    @property
    def durable(self) -> bool:
        return bool(getattr(self.store, "durable", False))

    def create(
        self,
        *,
        tenant_id: uuid.UUID,
        repository_id: uuid.UUID,
        failure_fingerprint: str,
        publication_timeout: timedelta = timedelta(days=7),
        repair_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        if not timedelta(seconds=1) <= publication_timeout <= _MAX_PUBLICATION_TIMEOUT:
            raise ValueError("publication timeout must be positive and at most 30 days")
        now = self.clock()
        repair_id = repair_id or uuid.uuid4()
        with self.store.lock:
            existing = self.store.records.get(repair_id)
            if existing is not None:
                if (
                    existing.tenant_id != tenant_id
                    or existing.repository_id != repository_id
                    or existing.failure_fingerprint != failure_fingerprint
                ):
                    raise ValueError("repair workflow identity conflicts")
                return repair_id
            record = RepairWorkflowRecord(
                id=repair_id,
                tenant_id=tenant_id,
                repository_id=repository_id,
                failure_fingerprint=failure_fingerprint,
                state="intake",
                state_version=0,
                state_hash="0" * 64,
                created_at=now,
                updated_at=now,
                publication_deadline=now + publication_timeout,
                total_cost_usd=Decimal("0"),
            )
            self.store.records[repair_id] = self._rehash(record)
        return repair_id

    async def accept_intake(
        self,
        *,
        repair_id: uuid.UUID,
        tenant_id: uuid.UUID,
        repository_handle: str,
        failure_fingerprint: str,
    ) -> uuid.UUID:
        repository_id = uuid.uuid5(
            tenant_id,
            f"loopguard-repository-handle:{repository_handle}",
        )
        accepted_id = self.create(
            repair_id=repair_id,
            tenant_id=tenant_id,
            repository_id=repository_id,
            failure_fingerprint=failure_fingerprint,
        )
        if self.workflow_controller is not None:
            await self.workflow_controller.start(self._required(accepted_id))
        return accepted_id

    async def advance(
        self,
        repair_id: uuid.UUID,
        activities: RepairActivities,
        *,
        stop_after: str | None = None,
    ) -> RepairWorkflowRecord:
        record = self._required(repair_id)
        if record.state in {"completed", "cancelled", "failed"}:
            return record
        if record.state == "awaiting_publication":
            if self.clock() > record.publication_deadline:
                await self._checkpoint(record, activities, "release_resources")
                return self._update(
                    record,
                    state="cancelled",
                    cancellation_reason="publication_approval_expired",
                    publication={"status": "expired"},
                )
            if record.approved_action_id is None:
                return record
            result = await self._checkpoint(record, activities, "publish")
            record = self._merge_payload(record, "publish", result.payload)
            return self._update(
                record,
                state="completed",
                publication={
                    "status": "draft_published",
                    "artifact_id": result.artifact_id,
                },
            )

        for activity in ACTIVITIES:
            existing = self.store.checkpoints.get((repair_id, activity))
            if existing is not None:
                record = self._merge_payload(record, activity, existing.payload)
                target_state = activity_state(activity)
                if _STATE_ORDER.get(target_state, -1) > _STATE_ORDER.get(record.state, -1):
                    record = self._update(record, state=target_state)
                if activity == "render_report":
                    record = self._update(
                        record,
                        publication={"status": "waiting_for_approval"},
                    )
                continue
            result = await self._checkpoint(record, activities, activity)
            cost = record.total_cost_usd + result.cost_usd
            if cost > self.maximum_cost_usd:
                await self._checkpoint(record, activities, "release_resources")
                return self._update(
                    record,
                    state="failed",
                    total_cost_usd=cost,
                    cancellation_reason="workflow_cost_budget_exceeded",
                )
            record = self._merge_payload(record, activity, result.payload)
            record = self._update(
                record,
                state=activity_state(activity),
                total_cost_usd=cost,
            )
            if activity == "render_report":
                record = self._update(
                    record,
                    publication={"status": "waiting_for_approval"},
                )
            if stop_after == activity:
                break
        return record

    async def signal_publish(
        self,
        repair_id: uuid.UUID,
        *,
        principal: Principal,
        expected_state_version: int,
        expected_state_hash: str,
        action_id: str,
    ) -> bool:
        record = self._required(repair_id)
        if not self._matches_action(
            record,
            principal=principal,
            permission=Permission.PUBLISH_REPAIR,
            expected_state_version=expected_state_version,
            expected_state_hash=expected_state_hash,
            allowed_states={"awaiting_publication"},
            require_unexpired=True,
        ):
            return False
        if record.approved_action_id is not None:
            return record.approved_action_id == action_id
        if self.workflow_controller is not None:
            await self.workflow_controller.authorize_publication(
                repair_id,
                action_id=action_id,
                expected_state_version=expected_state_version,
                expected_state_hash=expected_state_hash,
            )
        record = self._required(repair_id)
        if not self._matches_action(
            record,
            principal=principal,
            permission=Permission.PUBLISH_REPAIR,
            expected_state_version=expected_state_version,
            expected_state_hash=expected_state_hash,
            allowed_states={"awaiting_publication"},
            require_unexpired=True,
        ):
            return False
        self._update(record, approved_action_id=action_id)
        return True

    async def cancel(
        self,
        repair_id: uuid.UUID,
        *,
        principal: Principal,
        expected_state_version: int,
        expected_state_hash: str,
        action_id: str,
    ) -> bool:
        record = self._required(repair_id)
        if not self._matches_action(
            record,
            principal=principal,
            permission=Permission.RUN_REPAIR,
            expected_state_version=expected_state_version,
            expected_state_hash=expected_state_hash,
            allowed_states={
                "intake",
                "building_fixture",
                "reproducing",
                "planning",
                "generating",
                "evaluating",
                "ranking",
                "rendering_report",
                "awaiting_publication",
                "failed",
            },
        ):
            return False
        if self.workflow_controller is not None:
            await self.workflow_controller.cancel(repair_id)
        record = self._required(repair_id)
        if not self._matches_action(
            record,
            principal=principal,
            permission=Permission.RUN_REPAIR,
            expected_state_version=expected_state_version,
            expected_state_hash=expected_state_hash,
            allowed_states={
                "intake",
                "building_fixture",
                "reproducing",
                "planning",
                "generating",
                "evaluating",
                "ranking",
                "rendering_report",
                "awaiting_publication",
                "failed",
            },
        ):
            return False
        self._update(
            record,
            state="cancelled",
            approved_action_id=action_id,
            cancellation_reason="operator_cancelled",
        )
        return True

    async def retry(
        self,
        repair_id: uuid.UUID,
        *,
        principal: Principal,
        expected_state_version: int,
        expected_state_hash: str,
        action_id: str,
    ) -> bool:
        record = self._required(repair_id)
        if not self._matches_action(
            record,
            principal=principal,
            permission=Permission.RUN_REPAIR,
            expected_state_version=expected_state_version,
            expected_state_hash=expected_state_hash,
            allowed_states={"failed", "cancelled"},
        ):
            return False
        restarted = record.model_copy(
            update={
                "state": "intake",
                "state_version": record.state_version + 1,
                "approved_action_id": action_id,
                "cancellation_reason": None,
                "publication": {"status": "not_started"},
                "total_cost_usd": Decimal("0"),
                "updated_at": self.clock(),
            }
        )
        restarted = self._rehash(restarted)
        if self.workflow_controller is not None:
            await self.workflow_controller.retry(restarted)
        self._update(
            record,
            state="intake",
            approved_action_id=action_id,
            cancellation_reason=None,
            publication={"status": "not_started"},
            total_cost_usd=Decimal("0"),
        )
        return True

    def _matches_action(
        self,
        record: RepairWorkflowRecord,
        *,
        principal: Principal,
        permission: Permission,
        expected_state_version: int,
        expected_state_hash: str,
        allowed_states: set[str],
        require_unexpired: bool = False,
    ) -> bool:
        return (
            permission in principal.permissions
            and principal.tenant_id == record.tenant_id
            and record.state in allowed_states
            and (not require_unexpired or self.clock() <= record.publication_deadline)
            and record.state_version == expected_state_version
            and hmac.compare_digest(record.state_hash, expected_state_hash)
        )

    def read(self, tenant_id: uuid.UUID, repair_id: uuid.UUID) -> RepairWorkflowRecord | None:
        record = self.store.records.get(repair_id)
        return None if record is None or record.tenant_id != tenant_id else record

    def list_page(
        self,
        tenant_id: uuid.UUID,
        *,
        page_cursor: str | None,
        limit: int,
    ) -> tuple[list[RepairWorkflowRecord], str | None]:
        if not 1 <= limit <= 200:
            raise ValueError("repair page limit is invalid")
        boundary = self._decode_cursor(page_cursor) if page_cursor else None
        values = sorted(
            (record for record in self.store.records.values() if record.tenant_id == tenant_id),
            key=lambda record: (record.created_at, record.id),
            reverse=True,
        )
        if boundary is not None:
            values = [
                record
                for record in values
                if (record.created_at.isoformat(), str(record.id)) < boundary
            ]
        page = values[:limit]
        next_cursor = None
        if len(values) > limit and page:
            last = page[-1]
            next_cursor = self._encode_cursor(
                last.created_at.isoformat(),
                str(last.id),
            )
        return page, next_cursor

    def seed_detail(
        self,
        *,
        tenant_id: uuid.UUID,
        repository_id: uuid.UUID,
        failure_fingerprint: str,
        state: str,
        reproduction: dict[str, Any],
        candidates: list[dict[str, Any]],
        ranking: dict[str, Any],
        rollback: str,
        publication: dict[str, Any],
    ) -> uuid.UUID:
        repair_id = self.create(
            tenant_id=tenant_id,
            repository_id=repository_id,
            failure_fingerprint=failure_fingerprint,
        )
        record = self._required(repair_id)
        self._update(
            record,
            state=state,
            reproduction=reproduction,
            candidates=tuple(candidates),
            ranking=ranking,
            rollback=rollback,
            publication=publication,
        )
        return repair_id

    def summary(self, record: RepairWorkflowRecord) -> RepairSummary:
        return RepairSummary(
            id=record.id,
            repository_id=record.repository_id,
            state=record.state,
            failure_fingerprint=record.failure_fingerprint,
            created_at=record.created_at,
            updated_at=record.updated_at,
            winning_candidate_id=record.ranking.get("winning_candidate_id"),
        )

    def detail(self, record: RepairWorkflowRecord) -> RepairDetail:
        summary = self.summary(record).model_dump()
        return RepairDetail(
            **summary,
            state_version=record.state_version,
            state_hash=record.state_hash,
            reproduction=_bounded_public(record.reproduction),
            candidates=[_bounded_public(value) for value in record.candidates],
            ranking=_bounded_public(record.ranking),
            rollback=record.rollback,
            publication=_bounded_public(record.publication),
            capability={
                "available": self.workflow_registered,
                "reason": (
                    "workflow_registered" if self.workflow_registered else "workflow_unavailable"
                ),
            },
        )

    async def _checkpoint(
        self,
        record: RepairWorkflowRecord,
        activities: RepairActivities,
        activity: str,
    ) -> ActivityResult:
        key = (record.id, activity)
        existing = self.store.checkpoints.get(key)
        if existing is not None:
            return ActivityResult(
                artifact_id=existing.artifact_id,
                payload=existing.payload,
                cost_usd=existing.cost_usd,
            )
        result = await activities.run(activity, repair_id=record.id)
        checkpoint = ActivityCheckpoint(
            activity=activity,
            artifact_id=result.artifact_id,
            payload=result.payload,
            cost_usd=result.cost_usd,
            completed_at=self.clock(),
        )
        self.store.checkpoints[key] = checkpoint
        current = self._required(record.id)
        self.store.records[record.id] = self._rehash(
            current.model_copy(
                update={
                    "checkpoints": (*current.checkpoints, checkpoint),
                    "updated_at": self.clock(),
                }
            )
        )
        return result

    def _merge_payload(
        self,
        record: RepairWorkflowRecord,
        activity: str,
        payload: dict[str, Any],
    ) -> RepairWorkflowRecord:
        update: dict[str, Any] = {}
        if activity == "reproduce":
            update["reproduction"] = _bounded_public(payload)
        elif activity == "generate_candidates":
            update["candidates"] = tuple(
                _bounded_public(value) for value in payload.get("candidates", [])
            )
        elif activity == "rank":
            update["ranking"] = _bounded_public(payload)
        current = self._required(record.id)
        if not update or all(getattr(current, key) == value for key, value in update.items()):
            return current
        return self._update(current, **update)

    def _update(self, record: RepairWorkflowRecord, **values: Any) -> RepairWorkflowRecord:
        with self.store.lock:
            current = self._required(record.id)
            state_changed = values.get("state", current.state) != current.state
            updated = current.model_copy(
                update={
                    **values,
                    "state_version": current.state_version + int(state_changed),
                    "updated_at": self.clock(),
                }
            )
            updated = self._rehash(updated)
            self.store.records[record.id] = updated
            return updated

    def _required(self, repair_id: uuid.UUID) -> RepairWorkflowRecord:
        try:
            return self.store.records[repair_id]
        except KeyError as exc:
            raise ValueError("repair does not exist") from exc

    def _rehash(self, record: RepairWorkflowRecord) -> RepairWorkflowRecord:
        digest = hashlib.sha256(f"{record.state}:{record.state_version}".encode()).hexdigest()
        return record.model_copy(update={"state_hash": digest})

    def _encode_cursor(self, created_at: str, repair_id: str) -> str:
        payload = json.dumps([created_at, repair_id], separators=(",", ":")).encode()
        signature = hmac.new(self.cursor_secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def _decode_cursor(self, cursor: str) -> tuple[str, str]:
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(
                signature,
                hmac.new(self.cursor_secret, payload, hashlib.sha256).digest(),
            ):
                raise ValueError
            created_at, repair_id = json.loads(payload)
            datetime.fromisoformat(created_at)
            uuid.UUID(repair_id)
        except Exception as exc:
            raise ValueError("repair page cursor is invalid") from exc
        return str(created_at), str(repair_id)


def _bounded_public(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ValueError("repair client evidence is too deeply nested")
    if isinstance(value, dict):
        forbidden = (
            "rawfixture",
            "providertoken",
            "stacktrace",
            "environment",
            "credential",
            "secret",
        )
        return {
            str(key): _bounded_public(item, depth=depth + 1)
            for key, item in list(value.items())[:256]
            if not any(
                token in "".join(character for character in str(key).lower() if character.isalnum())
                for token in forbidden
            )
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_public(item, depth=depth + 1) for item in value[:256]]
    if isinstance(value, str):
        return value[:4_096]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    raise ValueError("repair client evidence contains an unsupported value")


class TemporalRepairWorkflowController:
    """Starts and signals the durable workflow with opaque, non-secret identifiers."""

    durable = True

    def __init__(self, client: Client, *, task_queue: str = "loopguard-repair-v1") -> None:
        if not task_queue or len(task_queue) > 256:
            raise ValueError("repair workflow task queue must be bounded")
        self.client = client
        self.task_queue = task_queue

    async def start(self, record: RepairWorkflowRecord) -> None:
        await self.client.start_workflow(
            TemporalRepairWorkflow.run,
            {
                "repair_id": str(record.id),
                "initial_state": record.state,
                "initial_state_version": record.state_version,
                "initial_state_hash": record.state_hash,
                "publication_timeout_seconds": int(
                    (record.publication_deadline - record.created_at).total_seconds()
                ),
            },
            id=_workflow_id(record.id),
            task_queue=self.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            search_attributes=_search_attributes(record),
        )

    async def authorize_publication(
        self,
        repair_id: uuid.UUID,
        *,
        action_id: str,
        expected_state_version: int,
        expected_state_hash: str,
    ) -> None:
        await self.client.get_workflow_handle(_workflow_id(repair_id)).signal(
            TemporalRepairWorkflow.authorize_publication,
            action_id,
            True,
            expected_state_version,
            expected_state_hash,
        )

    async def cancel(self, repair_id: uuid.UUID) -> None:
        await self.client.get_workflow_handle(_workflow_id(repair_id)).signal(
            TemporalRepairWorkflow.cancel
        )

    async def retry(self, record: RepairWorkflowRecord) -> None:
        await self.start(record)


def _workflow_id(repair_id: uuid.UUID) -> str:
    return f"repair/{repair_id}"


def _search_attributes(record: RepairWorkflowRecord) -> TypedSearchAttributes:
    return TypedSearchAttributes(
        [
            SearchAttributePair(
                SearchAttributeKey.for_keyword("LoopGuardTenantId"),
                str(record.tenant_id),
            ),
            SearchAttributePair(
                SearchAttributeKey.for_keyword("LoopGuardRepairId"),
                str(record.id),
            ),
            SearchAttributePair(
                SearchAttributeKey.for_keyword("LoopGuardRepairStatus"),
                record.state,
            ),
        ]
    )
