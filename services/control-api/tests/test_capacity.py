from __future__ import annotations

import hashlib
import uuid

import pytest
from pydantic import ValidationError

from loopguard_api.artifacts import (
    ArtifactIntegrityError,
    ArtifactService,
    MemoryKms,
    MemoryObjectStore,
)
from loopguard_api.capacity import CapacityExceeded, CapacityLimiter
from loopguard_api.errors import problem_response
from loopguard_api.settings import Settings


def limiter(*, clock=lambda: 0.0) -> CapacityLimiter:
    return CapacityLimiter(
        actions_per_minute=2,
        active_workflows=1,
        stream_connections=1,
        retry_min_seconds=3,
        retry_max_seconds=9,
        clock=clock,
    )


def test_action_rate_limit_recovers_after_window() -> None:
    now = 0.0
    tenant_id = uuid.uuid4()
    capacity = limiter(clock=lambda: now)
    capacity.admit_action(tenant_id)
    capacity.admit_action(tenant_id)

    with pytest.raises(CapacityExceeded) as overloaded:
        capacity.admit_action(tenant_id)
    assert overloaded.value.resource == "actions"
    assert 3 <= overloaded.value.retry_after_seconds <= 9

    now = 60.001
    capacity.admit_action(tenant_id)


def test_workflow_and_stream_limits_are_tenant_scoped_and_released() -> None:
    first_tenant = uuid.uuid4()
    second_tenant = uuid.uuid4()
    capacity = limiter()

    assert capacity.admit_workflow(first_tenant, "repair-1") is True
    assert capacity.admit_workflow(first_tenant, "repair-1") is False
    with pytest.raises(CapacityExceeded, match="workflows"):
        capacity.admit_workflow(first_tenant, "repair-2")
    assert capacity.admit_workflow(second_tenant, "repair-2") is True
    capacity.release_workflow(first_tenant, "repair-1")
    assert capacity.admit_workflow(first_tenant, "repair-2") is True

    with capacity.stream(first_tenant):
        with pytest.raises(CapacityExceeded, match="streams"):
            with capacity.stream(first_tenant):
                pass
        with capacity.stream(second_tenant):
            pass
    with capacity.stream(first_tenant):
        pass


def test_retry_guidance_is_stable_and_has_standard_header() -> None:
    tenant_id = uuid.uuid4()
    first = limiter()._exceeded(tenant_id, "actions")
    second = limiter()._exceeded(tenant_id, "actions")
    assert first.retry_after_seconds == second.retry_after_seconds

    response = problem_response(
        "LGAPI-OVERLOADED",
        "req_capacity",
        current_state={"resource": "actions"},
        retry_after_seconds=first.retry_after_seconds,
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == str(first.retry_after_seconds)


def test_settings_reject_invalid_retry_range() -> None:
    with pytest.raises(ValidationError, match="overload retry range"):
        Settings.for_test(
            overload_retry_min_seconds=30,
            overload_retry_max_seconds=3,
        )


def test_artifact_limit_rejects_before_presigning() -> None:
    service = ArtifactService(
        objects=MemoryObjectStore(),
        kms=MemoryKms(),
        maximum_bytes=8,
    )
    with pytest.raises(ArtifactIntegrityError, match="declaration"):
        service.initiate(
            tenant_id=uuid.uuid4(),
            declared_sha256=hashlib.sha256(b"123456789").hexdigest(),
            byte_count=9,
            media_type="application/octet-stream",
            retention_class="proof",
            expires_at=None,
        )
