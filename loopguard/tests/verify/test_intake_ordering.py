from __future__ import annotations

import asyncio
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from loopguard.verify.intake import IntakeMode, ProofIntakeService, is_mutating_tool
from loopguard.verify.models import (
    AcceptanceState,
    Baseline,
    CheckSpec,
    CheckResult,
    CheckStatus,
    ContractSource,
    IsolationLevel,
    ProofContract,
)


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _contract(event_id: str = "prompt-1", prompt_hash: str = "a" * 64) -> ProofContract:
    return ProofContract(
        task_id="task-1",
        source_event_id=event_id,
        source_prompt_hash=prompt_hash,
        source=ContractSource.CONTRACT_EVENT,
        source_hash="b" * 64,
        acceptance_state=AcceptanceState.CONFIRMED,
        acceptance=["Task is satisfied"],
        invariants=[],
        checks=[CheckSpec(id="unit", command=["pytest", "-q"])],
    )


def _baseline(event_id: str = "prompt-1") -> Baseline:
    return Baseline(
        baseline_id="baseline-1",
        captured_at=NOW,
        repository_sha="c" * 40,
        worktree_hash="d" * 64,
        owning_session_id="session-1",
        source_prompt_event_id=event_id,
        captured_before_first_mutation=True,
        results=[
            CheckResult(
                check_id="unit",
                status=CheckStatus.PASSED,
                started_at=NOW,
                completed_at=NOW,
                exit_code=0,
                isolation=IsolationLevel.SANDBOXED,
                worktree_hash="d" * 64,
            )
        ],
    )


class _BaselineService:
    def __init__(self):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.failure: Exception | None = None

    async def capture(self, contract, *, repository, owning_session_id):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.failure is not None:
            raise self.failure
        return _baseline(contract.source_event_id)


def test_managed_mutation_waits_for_exactly_one_pre_mutation_baseline(tmp_path: Path) -> None:
    async def scenario():
        baseline = _BaselineService()
        service = ProofIntakeService(baseline)
        await service.submit_prompt("session-1", _contract(), repository=tmp_path)
        first = asyncio.create_task(
            service.before_mutation("session-1", "mutation-1", mode=IntakeMode.MANAGED)
        )
        await baseline.started.wait()
        second = asyncio.create_task(
            service.before_mutation("session-1", "mutation-2", mode=IntakeMode.MANAGED)
        )
        await asyncio.sleep(0)
        assert not first.done()
        assert not second.done()
        baseline.release.set()
        return await first, await second, baseline.calls

    first, second, calls = asyncio.run(scenario())

    assert calls == 1
    assert first.allow is True
    assert second.allow is True
    assert first.baseline is not None


def test_prompt_replacement_before_mutation_replaces_stale_contract(tmp_path: Path) -> None:
    async def scenario():
        baseline = _BaselineService()
        baseline.release.set()
        service = ProofIntakeService(baseline)
        await service.submit_prompt("session-1", _contract(), repository=tmp_path)
        await service.submit_prompt(
            "session-1",
            _contract("prompt-2", "e" * 64),
            repository=tmp_path,
        )
        gate = await service.before_mutation(
            "session-1", "mutation-1", mode=IntakeMode.MANAGED
        )
        return gate, baseline.calls

    gate, calls = asyncio.run(scenario())

    assert calls == 1
    assert gate.baseline is not None
    assert gate.baseline.source_prompt_event_id == "prompt-2"


def test_prompt_replacement_during_baseline_cannot_attach_stale_evidence(tmp_path: Path) -> None:
    async def scenario():
        baseline = _BaselineService()
        service = ProofIntakeService(baseline)
        await service.submit_prompt("session-1", _contract(), repository=tmp_path)
        stale = asyncio.create_task(
            service.before_mutation("session-1", "mutation-old", mode=IntakeMode.MANAGED)
        )
        await baseline.started.wait()
        await service.submit_prompt(
            "session-1",
            _contract("prompt-2", "e" * 64),
            repository=tmp_path,
        )
        stale_gate = await stale
        baseline.release.set()
        current_gate = await service.before_mutation(
            "session-1", "mutation-new", mode=IntakeMode.MANAGED
        )
        return stale_gate, current_gate

    stale, current = asyncio.run(scenario())

    assert stale.allow is False
    assert stale.reason == "prompt_replaced_during_baseline"
    assert current.allow is True
    assert current.baseline is not None
    assert current.baseline.source_prompt_event_id == "prompt-2"


def test_managed_capture_failure_blocks_while_attached_warn_policy_is_inconclusive(
    tmp_path: Path,
) -> None:
    async def scenario():
        managed_baseline = _BaselineService()
        managed_baseline.failure = TimeoutError("baseline timed out")
        managed_baseline.release.set()
        managed = ProofIntakeService(managed_baseline)
        await managed.submit_prompt("managed", _contract(), repository=tmp_path)
        managed_gate = await managed.before_mutation(
            "managed", "mutation-1", mode=IntakeMode.MANAGED
        )

        attached_baseline = _BaselineService()
        attached_baseline.failure = TimeoutError("baseline timed out")
        attached_baseline.release.set()
        attached = ProofIntakeService(attached_baseline)
        await attached.submit_prompt("attached", _contract(), repository=tmp_path)
        attached_gate = await attached.before_mutation(
            "attached", "mutation-1", mode=IntakeMode.ATTACHED_WARN
        )
        return managed_gate, attached_gate

    managed, attached = asyncio.run(scenario())

    assert managed.allow is False
    assert managed.status == "baseline_failed"
    assert attached.allow is True
    assert attached.status == "inconclusive"


def test_required_timeout_cannot_be_treated_as_a_trusted_baseline(tmp_path: Path) -> None:
    class TimedOutBaseline(_BaselineService):
        async def capture(self, contract, *, repository, owning_session_id):
            baseline = _baseline(contract.source_event_id)
            timed_out = baseline.results[0].model_copy(
                update={"status": CheckStatus.TIMED_OUT, "exit_code": None}
            )
            return baseline.model_copy(update={"results": [timed_out]})

    async def scenario():
        service = ProofIntakeService(TimedOutBaseline())
        await service.submit_prompt("session-1", _contract(), repository=tmp_path)
        return await service.before_mutation(
            "session-1", "mutation-1", mode=IntakeMode.MANAGED
        )

    gate = asyncio.run(scenario())

    assert gate.allow is False
    assert gate.reason == "baseline_checks_incomplete"


def test_late_attach_can_never_claim_verified_baseline(tmp_path: Path) -> None:
    async def scenario():
        baseline = _BaselineService()
        service = ProofIntakeService(baseline)
        await service.record_late_attach(
            "session-1",
            _contract(),
            repository=tmp_path,
            observed_mutation_id="already-mutated",
        )
        return await service.before_mutation(
            "session-1", "mutation-2", mode=IntakeMode.ATTACHED_WARN
        )

    gate = asyncio.run(scenario())

    assert gate.allow is True
    assert gate.status == "inconclusive"
    assert gate.baseline is None


def test_daemon_restart_reuses_persisted_baseline_before_first_mutation(tmp_path: Path) -> None:
    async def scenario():
        journal = tmp_path / "state" / "intake.json"
        baseline = _BaselineService()
        baseline.release.set()
        first = ProofIntakeService(baseline, journal_path=journal)
        await first.submit_prompt("session-1", _contract(), repository=tmp_path)
        gate = await first.before_mutation(
            "session-1", "mutation-1", mode=IntakeMode.MANAGED
        )
        assert gate.allow is True
        restarted_baseline = _BaselineService()
        restarted = ProofIntakeService(restarted_baseline, journal_path=journal)
        repeated = await restarted.before_mutation(
            "session-1", "mutation-2", mode=IntakeMode.MANAGED
        )
        return repeated, restarted_baseline.calls

    gate, calls = asyncio.run(scenario())

    assert gate.allow is True
    assert gate.baseline is not None
    assert calls == 0


def test_intake_journal_is_private_and_rejects_mismatched_recovery_state(
    tmp_path: Path,
) -> None:
    async def persist() -> Path:
        journal = tmp_path / "state" / "intake.json"
        baseline = _BaselineService()
        baseline.release.set()
        service = ProofIntakeService(baseline, journal_path=journal)
        await service.submit_prompt("session-1", _contract(), repository=tmp_path)
        gate = await service.before_mutation(
            "session-1", "mutation-1", mode=IntakeMode.MANAGED
        )
        assert gate.allow is True
        return journal

    journal = asyncio.run(persist())
    if os.name == "posix":
        assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    payload = json.loads(journal.read_text())
    payload[0]["baseline"]["source_prompt_event_id"] = "different-prompt"
    journal.write_text(json.dumps(payload))

    try:
        ProofIntakeService(_BaselineService(), journal_path=journal)
    except ValueError as exc:
        assert "inconsistent proof state" in str(exc)
    else:
        raise AssertionError("mismatched baseline journal was trusted")


def test_prompt_hash_mismatch_and_mutation_classification_are_explicit(tmp_path: Path) -> None:
    async def mismatch():
        service = ProofIntakeService(_BaselineService())
        await service.submit_prompt(
            "session-1",
            _contract(),
            repository=tmp_path,
            raw_prompt="different prompt",
        )

    try:
        asyncio.run(mismatch())
    except ValueError as exc:
        assert "source prompt" in str(exc)
    else:
        raise AssertionError("prompt hash mismatch was accepted")
    assert is_mutating_tool("apply_patch", {}) is True
    assert is_mutating_tool("Bash", {"command": "git add file"}) is True
    assert is_mutating_tool("Bash", {"command": "pytest -q"}) is True
    assert is_mutating_tool("Bash", {}) is True
    assert is_mutating_tool("Read", {"file_path": "src/app.py"}) is False
