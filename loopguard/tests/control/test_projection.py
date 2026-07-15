from copy import deepcopy

import pytest

from loopguard import LoopGuard, LoopGuardConfig
from loopguard.control.projection import to_loop_event
from loopguard.control import to_loop_event as exported_to_loop_event
from loopguard.control.events import ControlEvent, EventKind, SessionRef


NON_PROJECTING_KINDS = (
    EventKind.SESSION_STARTED,
    EventKind.SESSION_STOPPED,
    EventKind.TURN_COMPLETED,
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


def test_missing_usage_defaults_to_zero():
    projected = to_loop_event(_event(EventKind.TOOL_CALL))

    assert projected is not None
    assert projected.tokens == 0
    assert projected.cost_usd == 0.0


@pytest.mark.parametrize(
    ("value", "case"),
    (
        (True, "boolean"),
        (None, "explicit-none"),
        ("many", "non-numeric"),
        (float("nan"), "nan"),
        ("nan", "nan-string"),
        (float("inf"), "positive-infinity"),
        ("inf", "positive-infinity-string"),
        (float("-inf"), "negative-infinity"),
        ("-inf", "negative-infinity-string"),
        (-1, "negative-integer"),
        ("-1", "negative-integer-string"),
        (1.5, "fractional"),
        ("1.5", "fractional-string"),
    ),
    ids=lambda parameter: parameter if isinstance(parameter, str) else None,
)
def test_invalid_tokens_raise_clear_value_error(value: object, case: str):
    event = _event(EventKind.TOOL_CALL, payload={"tokens": value})

    with pytest.raises(ValueError, match="tokens"):
        to_loop_event(event)


@pytest.mark.parametrize(
    ("value", "case"),
    (
        (True, "boolean"),
        (None, "explicit-none"),
        ("expensive", "non-numeric"),
        (float("nan"), "nan"),
        ("nan", "nan-string"),
        (float("inf"), "positive-infinity"),
        ("inf", "positive-infinity-string"),
        (float("-inf"), "negative-infinity"),
        ("-inf", "negative-infinity-string"),
        (-0.01, "negative"),
        ("-0.01", "negative-string"),
    ),
    ids=lambda parameter: parameter if isinstance(parameter, str) else None,
)
def test_invalid_cost_raises_clear_value_error(value: object, case: str):
    event = _event(EventKind.TOOL_CALL, payload={"cost_usd": value})

    with pytest.raises(ValueError, match="cost_usd"):
        to_loop_event(event)


def test_explicit_none_arguments_remain_none():
    event = _event(EventKind.TOOL_CALL, payload={"arguments": None})

    projected = to_loop_event(event)

    assert projected is not None
    assert projected.tool_args is None


@pytest.mark.parametrize("arguments", ([], "not-a-dict", 1))
def test_arguments_require_dict_or_none(arguments: object):
    event = _event(EventKind.TOOL_CALL, payload={"arguments": arguments})

    with pytest.raises(ValueError, match="arguments"):
        to_loop_event(event)


@pytest.mark.parametrize(
    "arguments",
    (
        {"nested": {1, 2}},
        {"nested": (1, 2)},
        {1: "non-string-key"},
    ),
)
def test_arguments_reject_non_json_nested_content(arguments: dict):
    event = _event(EventKind.TOOL_CALL, payload={"arguments": arguments})

    with pytest.raises(ValueError, match="arguments"):
        to_loop_event(event)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_arguments_reject_nested_non_finite_floats(value: float):
    event = _event(
        EventKind.TOOL_CALL,
        payload={"arguments": {"nested": {"values": [1, value]}}},
    )

    with pytest.raises(ValueError, match="arguments"):
        to_loop_event(event)


def test_mutating_projected_arguments_does_not_mutate_control_event():
    event = _event(
        EventKind.TOOL_CALL,
        payload={"arguments": {"env": {"CI": "1"}, "targets": ["unit"]}},
    )
    projected = to_loop_event(event)

    assert projected is not None
    assert projected.tool_args is not None
    projected.tool_args["env"]["CI"] = "0"
    projected.tool_args["targets"].append("integration")

    assert event.payload["arguments"] == {
        "env": {"CI": "1"},
        "targets": ["unit"],
    }


def test_mutating_control_event_arguments_does_not_mutate_projection():
    event = _event(
        EventKind.TOOL_CALL,
        payload={"arguments": {"env": {"CI": "1"}, "targets": ["unit"]}},
    )
    projected = to_loop_event(event)

    assert projected is not None
    assert projected.tool_args is not None
    event.payload["arguments"]["env"]["CI"] = "0"
    event.payload["arguments"]["targets"].append("integration")

    assert projected.tool_args == {
        "env": {"CI": "1"},
        "targets": ["unit"],
    }


def test_nan_cost_cannot_poison_or_bypass_guard_budget():
    guard = LoopGuard(
        LoopGuardConfig(
            action="pause",
            enable_budget=True,
            enable_exact=False,
            enable_pingpong=False,
            enable_semantic=False,
            enable_judge=False,
            max_tool_calls=None,
            max_cost_usd=1.0,
        ),
        on_pause=lambda decision: decision,
    )
    nan_event = _event(EventKind.TOOL_CALL, payload={"cost_usd": float("nan")})

    with pytest.raises(ValueError, match="cost_usd"):
        to_loop_event(nan_event)

    assert guard.summary()["events"] == 0

    valid_event = to_loop_event(_event(EventKind.TOOL_CALL, payload={"cost_usd": 1.01}))
    assert valid_event is not None
    decision = guard.observe(valid_event)

    assert decision.tripped is True
    assert decision.detector == "budget"
    assert guard.summary()["cost_usd"] == 1.01


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
