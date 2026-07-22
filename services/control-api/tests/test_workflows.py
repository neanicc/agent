from __future__ import annotations

from loopguard_api.notifications import NotificationService
from loopguard_api.workflows import WorkflowEngine


def test_notification_retry_does_not_duplicate_delivery():
    notifications = NotificationService()
    notifications.fail_first_attempt = True
    engine = WorkflowEngine(notifications=notifications, maximum_attempts=3)

    result = engine.notify_action(action_id="a1")

    assert result.status == "delivered"
    assert result.workflow_id == "notify-action/a1"
    assert notifications.accepted_ids == ["a1"]
    assert engine.notify_action(action_id="a1") == result


def test_workflow_ids_and_activity_keys_are_deterministic_and_secret_free():
    notifications = NotificationService()
    engine = WorkflowEngine(notifications=notifications)

    first = engine.start_child(kind="verification", domain_id="verification-1")
    duplicate = engine.start_child(kind="verification", domain_id="verification-1")
    cancelled = engine.cancel_child(kind="verification", domain_id="verification-1")

    assert first.workflow_id == "verification/verification-1"
    assert first.activity_idempotency_key == "verification:verification-1:start"
    assert duplicate == first
    assert cancelled.status == "cancelled"
    assert all("secret" not in repr(item).lower() for item in engine.history)


def test_action_expiry_and_artifact_deletion_workflows_are_idempotent():
    engine = WorkflowEngine(notifications=NotificationService())

    first = engine.expire_action(action_id="action-1")
    second = engine.expire_action(action_id="action-1")
    deleted = engine.delete_artifact(artifact_id="artifact-1")

    assert first == second
    assert first.workflow_id == "expire-action/action-1"
    assert deleted.workflow_id == "delete-artifact/artifact-1"
