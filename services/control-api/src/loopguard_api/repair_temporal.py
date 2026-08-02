from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


ACTIVITIES = (
    "intake",
    "build_fixture",
    "reproduce",
    "plan",
    "generate_candidates",
    "evaluate_candidates",
    "rank",
    "render_report",
)


def activity_state(activity: str) -> str:
    return {
        "intake": "building_fixture",
        "build_fixture": "reproducing",
        "reproduce": "planning",
        "plan": "generating",
        "generate_candidates": "evaluating",
        "evaluate_candidates": "ranking",
        "rank": "rendering_report",
        "render_report": "awaiting_publication",
    }[activity]


@workflow.defn(name="loopguard-repair-v1")
class TemporalRepairWorkflow:
    """Temporal-owned ordering; activities persist artifacts before returning."""

    def __init__(self) -> None:
        self.state = "created"
        self.state_version = 0
        self.state_hash = "0" * 64
        self._approved_action_id: str | None = None
        self._cancelled = False

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        repair_id = str(request["repair_id"])
        initial_state = str(request.get("initial_state", "intake"))
        initial_state_version = int(request.get("initial_state_version", 0))
        initial_state_hash = str(request.get("initial_state_hash", ""))
        expected_initial_hash = hashlib.sha256(
            f"{initial_state}:{initial_state_version}".encode()
        ).hexdigest()
        if (
            initial_state not in {"intake"}
            or initial_state_version < 0
            or not hmac.compare_digest(initial_state_hash, expected_initial_hash)
        ):
            raise ValueError("Temporal repair initial state is invalid")
        self.state = initial_state
        self.state_version = initial_state_version
        self.state_hash = initial_state_hash
        timeout_seconds = int(request.get("publication_timeout_seconds", 7 * 86_400))
        if not 1 <= timeout_seconds <= 30 * 86_400:
            raise ValueError("Temporal repair publication timeout is invalid")
        retry = RetryPolicy(maximum_attempts=3)
        artifacts: list[str] = []
        for activity in ACTIVITIES:
            result = await workflow.execute_activity(
                activity,
                {"repair_id": repair_id, "activity_id": f"{repair_id}:{activity}"},
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=retry,
            )
            artifact_id = str(result["artifact_id"])
            if not artifact_id:
                raise ValueError("repair activity returned without a persisted artifact")
            artifacts.append(artifact_id)
            self._transition(activity_state(activity))
        try:
            await workflow.wait_condition(
                lambda: self._approved_action_id is not None or self._cancelled,
                timeout=timedelta(seconds=timeout_seconds),
            )
        except TimeoutError:
            self._cancelled = True
        if self._cancelled:
            await workflow.execute_activity(
                "release_resources",
                {
                    "repair_id": repair_id,
                    "activity_id": f"{repair_id}:release_resources",
                },
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry,
            )
            self._transition("cancelled")
            return {"state": self.state, "artifact_ids": artifacts}
        publication = await workflow.execute_activity(
            "publish",
            {
                "repair_id": repair_id,
                "action_id": self._approved_action_id,
                "activity_id": f"{repair_id}:publish",
            },
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=retry,
        )
        publication_artifact_id = str(publication["artifact_id"])
        if not publication_artifact_id:
            raise ValueError("publication returned without a persisted artifact")
        artifacts.append(publication_artifact_id)
        self._transition("completed")
        return {"state": self.state, "artifact_ids": artifacts}

    @workflow.signal
    async def authorize_publication(
        self,
        action_id: str,
        authorized: bool,
        expected_state_version: int,
        expected_state_hash: str,
    ) -> None:
        if (
            authorized
            and self.state == "awaiting_publication"
            and expected_state_version == self.state_version
            and hmac.compare_digest(expected_state_hash, self.state_hash)
        ):
            self._approved_action_id = action_id

    @workflow.signal
    async def cancel(self) -> None:
        self._cancelled = True

    @workflow.query
    def repair_state(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "state_version": self.state_version,
            "state_hash": self.state_hash,
        }

    def _transition(self, state: str) -> None:
        self.state = state
        self.state_version += 1
        self.state_hash = hashlib.sha256(f"{state}:{self.state_version}".encode()).hexdigest()
