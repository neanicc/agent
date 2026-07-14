from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from loopguard.control.decisions import (
    ActionKind,
    ActionRequest,
    ActionTarget,
    PolicyDecision,
    TargetKind,
)


def _decision() -> PolicyDecision:
    return PolicyDecision(
        decision_id="dec_1",
        action="request_approval",
        reason="loop detected",
        target=ActionTarget(kind=TargetKind.SESSION, target_id="s_1"),
        state_version=4,
        state_hash="sha256:state-4",
    )


def _action(
    kind: ActionKind = ActionKind.INTERRUPT,
    target: ActionTarget | None = None,
) -> ActionRequest:
    return ActionRequest(
        action_id="act_1",
        target=target or ActionTarget(kind=TargetKind.SESSION, target_id="s_1"),
        kind=kind,
        expected_state_version=4,
        expected_state_hash="sha256:state-4",
        nonce="nonce_1",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )


def test_policy_decision_and_action_request_share_session_state():
    decision = _decision()
    action = ActionRequest(
        action_id="act_1",
        target=decision.target,
        kind=ActionKind.INTERRUPT,
        expected_state_version=decision.state_version,
        expected_state_hash=decision.state_hash,
        nonce="nonce_1",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    assert action.expected_state_version == 4
    assert action.target.target_id == "s_1"


@pytest.mark.parametrize("kind", list(ActionKind))
def test_every_action_kind_round_trips_through_json(kind: ActionKind):
    restored = ActionRequest.model_validate_json(_action(kind).model_dump_json())

    assert restored.kind is kind


@pytest.mark.parametrize("kind", list(TargetKind))
def test_every_target_kind_round_trips_through_json(kind: TargetKind):
    target = ActionTarget(kind=kind, target_id="target_1")
    restored = ActionTarget.model_validate_json(target.model_dump_json())

    assert restored.kind is kind


def test_policy_decision_rejects_unsupported_schema_version():
    payload = _decision().model_dump(mode="json")
    payload["schema_version"] = 2

    with pytest.raises(ValidationError, match="schema_version"):
        PolicyDecision.model_validate(payload)


def test_action_request_rejects_unsupported_schema_version():
    payload = _action().model_dump(mode="json")
    payload["schema_version"] = 2

    with pytest.raises(ValidationError, match="schema_version"):
        ActionRequest.model_validate(payload)


def test_action_target_rejects_invalid_kind():
    with pytest.raises(ValidationError, match="kind"):
        ActionTarget(kind="organization", target_id="org_1")


def test_action_request_rejects_state_hash_mismatch():
    with pytest.raises(ValueError, match="state mismatch"):
        _action().validate_state(
            current_state_version=4,
            current_state_hash="sha256:different-state",
        )


def test_action_request_accepts_matching_state():
    _action().validate_state(
        current_state_version=4,
        current_state_hash="sha256:state-4",
    )


def test_action_request_rejects_unknown_top_level_field():
    payload = _action().model_dump(mode="json")
    payload["unexpected"] = "not part of the envelope"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ActionRequest.model_validate(payload)


def test_policy_decision_rejects_unknown_top_level_field():
    payload = _decision().model_dump(mode="json")
    payload["unexpected"] = "not part of the envelope"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PolicyDecision.model_validate(payload)


def test_action_request_rejects_unknown_nested_target_field():
    payload = _action().model_dump(mode="json")
    payload["target"]["repository_id"] = "repo_1"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ActionRequest.model_validate(payload)


def test_action_request_parameters_remain_open_to_action_fields():
    payload = _action().model_dump(mode="json")
    payload["parameters"]["provider_extension"] = {"nested": "value"}

    restored = ActionRequest.model_validate(payload)

    assert restored.parameters["provider_extension"] == {"nested": "value"}


def test_action_request_rejects_naive_expires_at():
    payload = _action().model_dump(mode="json")
    payload["expires_at"] = datetime(2026, 7, 14, 12, 0)

    with pytest.raises(ValidationError, match="timezone"):
        ActionRequest.model_validate(payload)


def test_action_request_preserves_offset_aware_expiry_through_json():
    offset = timezone(timedelta(hours=5, minutes=30))
    expires_at = datetime(2026, 7, 14, 17, 30, tzinfo=offset)
    action = _action().model_copy(update={"expires_at": expires_at})

    restored = ActionRequest.model_validate_json(action.model_dump_json())

    assert restored.expires_at == expires_at
    assert restored.expires_at.utcoffset() == timedelta(hours=5, minutes=30)


def test_action_request_rejects_state_version_mismatch():
    with pytest.raises(ValueError, match="state mismatch"):
        _action().validate_state(
            current_state_version=5,
            current_state_hash="sha256:state-4",
        )
