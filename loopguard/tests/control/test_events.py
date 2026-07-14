import pytest
from pydantic import ValidationError

from loopguard.control.events import ControlEvent, EventKind, SessionRef


def _event(kind: EventKind = EventKind.TOOL_CALL) -> ControlEvent:
    return ControlEvent(
        event_id="evt_01",
        kind=kind,
        source="codex",
        session=SessionRef(host_id="host_1", repo_id="repo_1", session_id="s_1"),
        payload={"tool_name": "Bash", "arguments": {"cmd": "pytest"}},
    )


def test_control_event_round_trips_with_version():
    restored = ControlEvent.model_validate_json(_event().model_dump_json())

    assert restored.schema_version == 1
    assert restored.session.repo_id == "repo_1"
    assert restored.payload["tool_name"] == "Bash"


@pytest.mark.parametrize("kind", list(EventKind))
def test_every_event_kind_round_trips_through_json(kind: EventKind):
    restored = ControlEvent.model_validate_json(_event(kind).model_dump_json())

    assert restored.kind is kind


def test_control_event_rejects_unsupported_schema_version():
    payload = _event().model_dump(mode="json")
    payload["schema_version"] = 2

    with pytest.raises(ValidationError, match="schema_version"):
        ControlEvent.model_validate(payload)
