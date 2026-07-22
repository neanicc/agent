from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from .notifications import NotificationDeliveryError, NotificationService


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    workflow_id: str
    activity_idempotency_key: str
    status: str


class WorkflowEngine:
    """Deterministic workflow logic shared by tests and the Temporal worker adapter."""

    def __init__(
        self, *, notifications: NotificationService, maximum_attempts: int = 3
    ) -> None:
        if maximum_attempts < 1 or maximum_attempts > 10:
            raise ValueError("workflow retry attempts must be bounded")
        self.notifications = notifications
        self.maximum_attempts = maximum_attempts
        self._results: dict[str, WorkflowResult] = {}
        self.history: list[WorkflowResult] = []

    def notify_action(self, *, action_id: str) -> WorkflowResult:
        workflow_id = f"notify-action/{action_id}"
        existing = self._results.get(workflow_id)
        if existing is not None:
            return existing
        key = f"notify-action:{action_id}"
        for attempt in range(1, self.maximum_attempts + 1):
            try:
                self.notifications.deliver_action(
                    action_id=action_id, idempotency_key=key
                )
                break
            except NotificationDeliveryError:
                if attempt == self.maximum_attempts:
                    raise
        return self._record(WorkflowResult(workflow_id, key, "delivered"))

    def expire_action(self, *, action_id: str) -> WorkflowResult:
        return self._once("expire-action", action_id, "expired")

    def delete_artifact(self, *, artifact_id: str) -> WorkflowResult:
        return self._once("delete-artifact", artifact_id, "deleted")

    def start_child(
        self,
        *,
        kind: Literal["verification", "repair"],
        domain_id: str,
    ) -> WorkflowResult:
        return self._once(kind, domain_id, "running")

    def cancel_child(
        self,
        *,
        kind: Literal["verification", "repair"],
        domain_id: str,
    ) -> WorkflowResult:
        workflow_id = f"{kind}/{domain_id}"
        current = self._results.get(workflow_id)
        if current is None:
            current = self.start_child(kind=kind, domain_id=domain_id)
        if current.status == "cancelled":
            return current
        cancelled = replace(current, status="cancelled")
        self._results[workflow_id] = cancelled
        self.history.append(cancelled)
        return cancelled

    def _once(self, kind: str, domain_id: str, status: str) -> WorkflowResult:
        if not domain_id or any(value in domain_id.lower() for value in ("secret", "token")):
            raise ValueError("workflow inputs must be opaque domain IDs without secrets")
        workflow_id = f"{kind}/{domain_id}"
        existing = self._results.get(workflow_id)
        if existing is not None:
            return existing
        return self._record(
            WorkflowResult(
                workflow_id,
                f"{kind}:{domain_id}:start",
                status,
            )
        )

    def _record(self, result: WorkflowResult) -> WorkflowResult:
        self._results[result.workflow_id] = result
        self.history.append(result)
        return result
