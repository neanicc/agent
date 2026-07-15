from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from loopguard.adapters.base import (
    AdapterCapabilities,
    AgentAdapter,
    Capability,
    CapabilityError,
    LifecycleError,
    LifecycleErrorCode,
    ManagedRunRequest,
)


def test_capability_set_is_explicit_and_immutable():
    source = {Capability.OBSERVE, Capability.BLOCK_TOOL}
    caps = AdapterCapabilities(surface="codex-hooks", supported=source)

    assert caps.supports(Capability.OBSERVE)
    assert not caps.supports(Capability.SELECT_MODEL)
    assert caps.require(Capability.OBSERVE) is None
    assert caps.require(Capability.SELECT_MODEL) == CapabilityError(
        code="capability_unavailable",
        capability=Capability.SELECT_MODEL,
        surface="codex-hooks",
    )
    source.add(Capability.SELECT_MODEL)
    assert not caps.supports(Capability.SELECT_MODEL)
    assert isinstance(caps.supported, frozenset)


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (LifecycleErrorCode.UNKNOWN_SESSION, False),
        (LifecycleErrorCode.STALE_STATE, False),
        (LifecycleErrorCode.PROCESS_EXITED, False),
        (LifecycleErrorCode.TIMEOUT, True),
        (LifecycleErrorCode.PROTOCOL_VERSION, False),
    ],
)
def test_lifecycle_failures_have_stable_typed_codes(code, retryable):
    error = LifecycleError.for_code(code, surface="managed-test", session_id="session-1")

    assert error.code is code
    assert error.retryable is retryable
    assert error.surface == "managed-test"
    assert error.session_id == "session-1"
    assert error.message


def test_managed_run_request_carries_every_safety_boundary(tmp_path):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    request = ManagedRunRequest(
        repository_id="repo-1",
        repository_root=repository,
        worktree_id="worktree-1",
        worktree_root=worktree,
        prompt="Fix the verified regression.",
        model="test-model",
        effort="medium",
        sandbox="workspace-write",
        permission_policy="on-request",
        proof_contract_id="proof-1",
        max_tokens=20_000,
        max_cost_usd=2.5,
    )

    assert request.repository_root == repository
    assert request.worktree_root == worktree
    assert request.max_tokens == 20_000
    assert request.max_cost_usd == 2.5
    assert request.model_dump(mode="json")["schema_version"] == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"repository_id": ""},
        {"repository_root": Path("relative/repository")},
        {"worktree_root": Path("relative/worktree")},
        {"prompt": ""},
        {"max_tokens": 0},
        {"max_cost_usd": -0.01},
        {"unknown": "silently accepted"},
    ],
)
def test_managed_run_request_rejects_ambiguous_or_unbounded_values(tmp_path, updates):
    values = {
        "repository_id": "repo-1",
        "repository_root": tmp_path / "repository",
        "worktree_id": "worktree-1",
        "worktree_root": tmp_path / "worktree",
        "prompt": "Fix the verified regression.",
        "model": "test-model",
        "effort": "medium",
        "sandbox": "workspace-write",
        "permission_policy": "on-request",
        "proof_contract_id": "proof-1",
        "max_tokens": 20_000,
        "max_cost_usd": 2.5,
    }
    values.update(updates)

    with pytest.raises(ValidationError):
        ManagedRunRequest.model_validate(values)


def test_observation_only_adapter_is_protocol_conformant_and_never_silently_noops():
    adapter = _ObservationOnlyAdapter()

    assert isinstance(adapter, AgentAdapter)
    assert adapter.unsupported(Capability.START_MANAGED).code == "capability_unavailable"
    assert adapter.unsupported(Capability.INTERRUPT).capability is Capability.INTERRUPT
    assert adapter.unsupported(Capability.INJECT_CONTEXT).capability is Capability.INJECT_CONTEXT
    assert adapter.unsupported(Capability.APPROVE_TOOL).capability is Capability.APPROVE_TOOL


class _ObservationOnlyAdapter:
    capabilities = AdapterCapabilities(
        surface="observation-only-test",
        supported={Capability.OBSERVE},
    )

    async def attach(self, session):
        return None

    async def start(self, request):
        return self.unsupported(Capability.START_MANAGED)

    async def interrupt(self, session):
        return self.unsupported(Capability.INTERRUPT)

    async def inject(self, session, context):
        return self.unsupported(Capability.INJECT_CONTEXT)

    async def resolve_action(self, session, action):
        return self.unsupported(Capability.APPROVE_TOOL)

    async def events(self, session):
        if False:
            yield

    def unsupported(self, capability: Capability) -> CapabilityError:
        error = self.capabilities.require(capability)
        assert error is not None
        return error
