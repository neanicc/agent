from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class EventKind(StrEnum):
    SESSION_STARTED = "session.started"
    SESSION_STOPPED = "session.stopped"
    PROMPT_SUBMITTED = "prompt.submitted"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    FILE_CHANGED = "file.changed"
    TEST_COMPLETED = "test.completed"
    PIPELINE_FAILED = "pipeline.failed"
    ACTION_REQUESTED = "action.requested"
    ACTION_RESOLVED = "action.resolved"


class SessionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_id: str
    repo_id: str
    session_id: str
    worktree_id: str | None = None
    turn_id: str | None = None


class ControlEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    event_id: str
    kind: EventKind
    source: str
    session: SessionRef
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
