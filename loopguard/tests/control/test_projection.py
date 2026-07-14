from copy import deepcopy

import pytest

from loopguard.control.projection import to_loop_event
from loopguard.control import to_loop_event as exported_to_loop_event
from loopguard.control.events import ControlEvent, EventKind, SessionRef


NON_PROJECTING_KINDS = (
    EventKind.SESSION_STARTED,
    EventKind.SESSION_STOPPED,
    EventKind.PROMPT_SUBMITTED,
    EventKind.FILE_CHANGED,
    EventKind.TEST_COMPLETED,
    EventKind.PIPELINE_FAILED,
    EventKind.ACTION_REQUESTED,
    EventKind.ACTION_RESOLVED,
)


def _event(kind: EventKind, *, payload: dict | None = None) -> ControlEvent:
    return ControlEvent(
        event_id="evt_1",
        kind=kind,
        source="claude",
        session=SessionRef(
            host_id="host_1",
            repo_id="repo_1",
            session_id="session_1",
            worktree_id="worktree_1",
        ),
        payload=payload or {},
    )


def test_projection_is_exported_from_control_package():
    assert exported_to_loop_event is to_loop_event


def test_tool_call_projects_payload_fields_and_metadata():
    event = _event(
        EventKind.TOOL_CALL,
        payload={
            "agent": "claude-code",
            "tool_name": "Bash",
            "arguments": {"cmd": "pytest"},
            "tokens": "12",
            "cost_usd": "0.25",
        },
    )

    projected = to_loop_event(event)

    assert projected is not None
    assert projected.run_id == "session_1"
    assert projected.agent == "claude-code"
    assert projected.kind == "tool_call"
    assert projected.tool_name == "Bash"
    assert projected.tool_args == {"cmd": "pytest"}
    assert projected.error is None
    assert projected.tokens == 12
    assert projected.cost_usd == 0.25
    assert projected.metadata == {
        "control_event_id": "evt_1",
        "repo_id": "repo_1",
        "worktree_id": "worktree_1",
    }


def test_tool_result_uses_source_agent_fallback_and_maps_result_fields():
    event = _event(
        EventKind.TOOL_RESULT,
        payload={
            "tool_name": "Bash",
            "arguments": {"cmd": "pytest"},
            "error": "command failed",
            "tokens": 7,
            "cost_usd": 0.5,
        },
    )

    projected = to_loop_event(event)

    assert projected is not None
    assert projected.agent == "claude"
    assert projected.kind == "tool_result"
    assert projected.tool_name == "Bash"
    assert projected.tool_args == {"cmd": "pytest"}
    assert projected.error == "command failed"
    assert projected.tokens == 7
    assert projected.cost_usd == 0.5


@pytest.mark.parametrize("kind", NON_PROJECTING_KINDS)
def test_non_tool_control_events_do_not_project(kind: EventKind):
    event = _event(kind, payload={"tool_name": "Bash"})

    assert to_loop_event(event) is None


def test_non_projecting_kinds_cover_every_other_event_kind():
    assert set(NON_PROJECTING_KINDS) == set(EventKind) - {
        EventKind.TOOL_CALL,
        EventKind.TOOL_RESULT,
    }


@pytest.mark.parametrize("kind", (EventKind.TOOL_CALL, EventKind.TOOL_RESULT))
def test_projection_does_not_mutate_control_event_payload(kind: EventKind):
    event = _event(
        kind,
        payload={
            "agent": "claude-code",
            "tool_name": "Bash",
            "arguments": {"cmd": "pytest", "env": {"CI": "1"}},
            "error": None,
            "tokens": "12",
            "cost_usd": "0.25",
        },
    )
    payload_before = deepcopy(event.payload)

    to_loop_event(event)

    assert event.payload == payload_before
