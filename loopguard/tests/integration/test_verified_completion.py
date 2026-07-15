from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from loopguard.adapters.hook_entry import process_hook
from loopguard.adapters.normalize_hook import RepositoryIdentity
from loopguard.control.decisions import ActionTarget, PolicyDecision, TargetKind
from loopguard.control.daemon import DaemonServices, LoopGuardDaemon
from loopguard.control.events import ControlEvent, EventKind, SessionRef
from loopguard.control.store import EventStore
from loopguard.verify.hooks import (
    CompletionEnforcement,
    CompletionEnforcer,
    CompletionLedger,
    VerificationWorkflow,
    canonical_repository_changes,
    stop_decision,
)
from loopguard.verify.baseline import capture_repository_snapshot
from loopguard.verify.intake import IntakeMode, ProofIntakeService
from loopguard.verify.models import (
    AcceptanceState,
    Baseline,
    CheckResult,
    CheckSpec,
    CheckStatus,
    ContractSource,
    IsolationLevel,
    ProofContract,
    RunStatus,
    VerificationRun,
    VerificationVerdict,
    VerdictStatus,
)
from loopguard.verify.runner import CheckExecution, ExecutionAuthorization
from loopguard.verify.service import VerificationService
from loopguard.verify.plugins import ChangeRecord
from tests.control.daemon_test_support import send_event, short_socket_path


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _verdict(
    status: VerdictStatus,
    *,
    introduced: list[str] | None = None,
) -> VerificationVerdict:
    return VerificationVerdict(
        status=status,
        introduced_failures=introduced or [],
        evidence_ids=["artifact-proof"],
    )


def test_stop_blocks_when_required_check_has_new_failure() -> None:
    decision = stop_decision(
        _verdict(VerdictStatus.REGRESSION, introduced=["tests/test_api.py::test_login"])
    )

    assert decision.allow is False
    assert decision.severity == "error"
    assert "new failure" in decision.message.lower()
    assert "tests/test_api.py::test_login" in decision.message


def test_observe_only_mode_warns_without_blocking() -> None:
    decision = stop_decision(
        _verdict(VerdictStatus.REGRESSION, introduced=["tests/test_api.py::test_login"]),
        enforcement=CompletionEnforcement.OBSERVE,
    )

    assert decision.allow is True
    assert decision.severity == "warning"
    assert decision.verified is False


def test_checks_passed_without_owned_baseline_are_never_reported_as_verified() -> None:
    decision = stop_decision(_verdict(VerdictStatus.CHECKS_PASSED_UNBASELINED))

    assert decision.allow is False
    assert decision.verified is False
    assert decision.severity == "warning"
    assert "baseline" in decision.message.lower()


@pytest.mark.parametrize("vendor", ["codex", "claude"])
def test_native_stop_uses_documented_block_response(vendor: str, tmp_path: Path) -> None:
    result = process_hook(
        vendor,
        "Stop",
        {
            "session_id": "vendor-session",
            "cwd": str(tmp_path),
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        },
        client=_PolicyClient("pause", "Verification found new failure(s): unit::failure"),
        identity_resolver=lambda _cwd: RepositoryIdentity(
            root=tmp_path,
            repo_id="repo",
            worktree_id="worktree",
        ),
        host_id="host",
    )

    assert result.blocked is True
    assert json.loads(result.stdout) == {
        "decision": "block",
        "reason": "Verification found new failure(s): unit::failure",
    }


def test_managed_completion_allows_only_two_repairs_across_restart(tmp_path: Path) -> None:
    run = _run(_verdict(VerdictStatus.REGRESSION, introduced=["unit::new_failure"]))
    reader = _Reader(run)
    ledger_path = tmp_path / "state" / "completion.json"
    ledger = CompletionLedger(ledger_path, integrity_key=b"completion-test-key")
    first = CompletionEnforcer(reader, ledger=ledger)
    first.bind("session", run.run_id, enforcement=CompletionEnforcement.MANAGED)
    core = _core_decision()

    one = first.policy_decision(_stop_event("stop-1"), core)
    duplicate = first.policy_decision(_stop_event("stop-1"), core)
    two = first.policy_decision(_stop_event("stop-2"), core)

    restarted = CompletionEnforcer(
        reader,
        ledger=CompletionLedger(
            ledger_path,
            integrity_key=b"completion-test-key",
        ),
    )
    three = restarted.policy_decision(_stop_event("stop-3"), core)
    replayed_one = restarted.policy_decision(_stop_event("stop-1"), core)

    assert [one.action, duplicate.action, two.action, three.action, replayed_one.action] == [
        "inject",
        "inject",
        "inject",
        "pause",
        "inject",
    ]
    assert one.metadata["completion"]["repair_attempt"] == 1
    assert duplicate.metadata["completion"]["repair_attempt"] == 1
    assert two.metadata["completion"]["repair_attempt"] == 2
    assert three.metadata["completion"]["outcome"] == "human_required"
    assert replayed_one.metadata["completion"]["repair_attempt"] == 1
    assert ledger_path.stat().st_mode & 0o777 == 0o600


def test_persistent_completion_ledger_rejects_missing_wrong_or_tampered_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "completion.json"
    with pytest.raises(ValueError, match="integrity key"):
        CompletionLedger(path)
    ledger = CompletionLedger(path, integrity_key=b"correct-key")
    ledger.bind("session", "run", CompletionEnforcement.BLOCK)
    with pytest.raises(ValueError, match="integrity"):
        CompletionLedger(path, integrity_key=b"wrong-key")
    payload = json.loads(path.read_text())
    payload["state"]["bindings"]["session"]["run_id"] = "different-run"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="integrity"):
        CompletionLedger(path, integrity_key=b"correct-key")


def test_daemon_services_register_proof_pipeline_and_overlay_stop_policy() -> None:
    run = _run(_verdict(VerdictStatus.REGRESSION, introduced=["unit::new_failure"]))
    enforcer = CompletionEnforcer(_Reader(run))
    enforcer.bind("session", run.run_id)
    intake = object()
    runner = object()
    artifacts = object()
    verdicts = object()
    services = DaemonServices(
        proof_intake=intake,
        runner=runner,
        artifact_store=artifacts,
        verdict_service=verdicts,
        completion_enforcement=enforcer,
    )

    decision = asyncio.run(services.apply_policy(_stop_event("stop"), _core_decision()))

    assert services.proof_intake is intake
    assert services.runner is runner
    assert services.artifact_store is artifacts
    assert services.verdict_service is verdicts
    assert decision.action == "pause"
    assert decision.state_version == 7
    assert decision.state_hash == "core-state"
    assert decision.metadata["completion"]["status"] == "regression"


def test_missing_verification_record_blocks_instead_of_failing_open() -> None:
    completion = CompletionEnforcer(_FailingReader())
    completion.bind("session", "missing-run")

    decision = completion.policy_decision(_stop_event("missing-stop"), _core_decision())

    assert decision.action == "pause"
    assert decision.metadata["completion"]["status"] == "inconclusive"
    assert decision.metadata["completion"]["verified"] is False


@pytest.mark.skipif(os.name != "posix", reason="native daemon integration uses a Unix socket")
def test_daemon_returns_normalized_verification_policy_for_stop(tmp_path: Path) -> None:
    async def scenario():
        run = _run(_verdict(VerdictStatus.REGRESSION, introduced=["unit::new_failure"]))
        completion = CompletionEnforcer(_Reader(run))
        completion.bind("session", run.run_id)
        with short_socket_path() as socket_path:
            store = EventStore.for_test(tmp_path / "events.db")
            daemon = LoopGuardDaemon(
                store=store,
                socket_path=socket_path,
                services=DaemonServices(completion_enforcement=completion),
            )
            await daemon.start()
            try:
                response = await send_event(socket_path, _stop_event("daemon-stop"))
                return response.payload["decision"]
            finally:
                await daemon.close()
                store.close()

    decision = asyncio.run(scenario())

    assert decision["action"] == "pause"
    assert decision["target"] == {"kind": "session", "target_id": "session"}
    assert decision["metadata"]["completion"]["status"] == "regression"


@pytest.mark.parametrize(
    ("journal_changes", "expected_iteration_source"),
    [
        (None, "git_fallback"),
        ([ChangeRecord(path="app.py")], "change_journal"),
    ],
)
def test_prompt_to_diff_to_signed_regression_proof_blocks_completion(
    tmp_path: Path,
    journal_changes: list[ChangeRecord] | None,
    expected_iteration_source: str,
) -> None:
    async def scenario():
        repository = _git_repo(tmp_path / "repo")
        prompt_text = "Change the greeting without breaking the existing test."
        prompt = _prompt_event(prompt_text)
        contract = _contract(prompt.event_id, prompt_text)
        verification = VerificationService.for_path(tmp_path / "verification.db")
        baseline_capture = _PersistedBaselineCapture(verification)
        intake = ProofIntakeService(
            baseline_capture,
            journal_path=tmp_path / "state" / "intake.json",
        )
        runner = _CurrentRunner(repository, failures=["tests/test_app.py::test_greeting"])
        completion = CompletionEnforcer(
            verification,
            ledger=CompletionLedger(
                tmp_path / "state" / "completion.json",
                integrity_key=b"completion-test-key",
            ),
        )
        workflow = VerificationWorkflow(
            proof_intake=intake,
            runner=runner,
            verification=verification,
            completion=completion,
            authorization_factory=lambda _check_id: ExecutionAuthorization(approved=True),
        )
        services = DaemonServices.from_verification_workflow(workflow)
        run_id = await services.submit_prompt(prompt, contract, repository=repository)
        baseline_capture.run_id = run_id
        mutation = _mutation_event("mutation-1")
        mutation_decision = await services.apply_policy(mutation, _core_decision())
        baseline = verification.read(run_id).baseline
        (repository / "app.py").write_text("GREETING = 'production'\n")
        completed = await services.verify_completion(
            "session",
            acceptance_evidence=[b"acceptance: greeting behavior reviewed"],
            journal_changes=journal_changes,
        )
        decision = await services.apply_policy(_stop_event("stop-e2e"), _core_decision())
        manifests = verification.store.manifests_for_run(run_id)
        change_manifest = next(item for item in manifests if item.command_id == "change-set")
        change_payload = json.loads(verification.read_artifact(change_manifest.manifest_id))
        return mutation_decision, baseline, completed, decision, change_payload, manifests

    mutation_decision, baseline, completed, decision, change_payload, manifests = asyncio.run(
        scenario()
    )

    assert mutation_decision.action == "allow"
    assert baseline is not None
    assert completed.verdict is not None
    assert completed.verdict.status is VerdictStatus.REGRESSION
    assert completed.verdict.introduced_failures == ["tests/test_app.py::test_greeting"]
    assert decision.action == "pause"
    assert decision.metadata["completion"]["evidence_ids"]
    assert change_payload["iteration_source"] == expected_iteration_source
    assert change_payload["canonical_repository_changes"][0]["path"] == "app.py"
    assert all(manifest.signature for manifest in manifests)


def test_daemon_service_blocks_first_mutation_when_baseline_capture_fails(
    tmp_path: Path,
) -> None:
    async def scenario():
        repository = _git_repo(tmp_path / "repo")
        prompt_text = "Change the greeting."
        prompt = _prompt_event(prompt_text)
        verification = VerificationService.for_path(tmp_path / "failed-baseline.db")
        intake = ProofIntakeService(_FailingBaselineCapture())
        runner = _CurrentRunner(repository)
        workflow = VerificationWorkflow(
            proof_intake=intake,
            runner=runner,
            verification=verification,
            completion=CompletionEnforcer(verification),
            authorization_factory=lambda _check_id: ExecutionAuthorization(approved=True),
        )
        services = DaemonServices.from_verification_workflow(workflow)
        await services.submit_prompt(
            prompt,
            _contract(prompt.event_id, prompt_text),
            repository=repository,
            mode=IntakeMode.MANAGED,
        )
        return await services.apply_policy(_mutation_event("blocked-mutation"), _core_decision())

    decision = asyncio.run(scenario())

    assert decision.action == "pause"
    assert decision.metadata["verification_intake"] == {
        "status": "baseline_failed",
        "reason": "baseline_capture_failed",
    }


def test_late_attach_pass_is_inconclusive_and_never_green(tmp_path: Path) -> None:
    async def scenario():
        repository = _git_repo(tmp_path / "repo")
        prompt_text = "Inspect the already changed greeting."
        prompt = _prompt_event(prompt_text)
        contract = _contract(prompt.event_id, prompt_text)
        verification = VerificationService.for_path(tmp_path / "late.db")
        intake = ProofIntakeService(_UnusedBaselineCapture())
        runner = _CurrentRunner(repository)
        completion = CompletionEnforcer(verification)
        workflow = VerificationWorkflow(
            proof_intake=intake,
            runner=runner,
            verification=verification,
            completion=completion,
            authorization_factory=lambda _check_id: ExecutionAuthorization(approved=True),
        )
        await workflow.attach_after_mutation(
            prompt,
            contract,
            repository=repository,
            observed_mutation_id="mutation-before-attach",
            mode=IntakeMode.ATTACHED_WARN,
        )
        completed = await workflow.verify_completion(
            "session",
            acceptance_evidence=[b"acceptance evidence"],
        )
        decision = completion.policy_decision(_stop_event("late-stop"), _core_decision())
        return completed, decision

    completed, decision = asyncio.run(scenario())

    assert completed.verdict is not None
    assert completed.verdict.status is VerdictStatus.CHECKS_PASSED_UNBASELINED
    assert decision.action == "warn"
    assert decision.metadata["completion"]["verified"] is False


def test_canonical_change_fallback_is_deterministic_without_context(tmp_path: Path) -> None:
    repository = _git_repo(tmp_path / "repo")
    (repository / "z.py").write_text("z = 1\n")
    (repository / "app.py").write_text("GREETING = 'changed'\n")

    first = canonical_repository_changes(repository)
    second = canonical_repository_changes(repository)

    assert [change.path for change in first] == ["app.py", "z.py"]
    assert first == second


def test_canonical_change_fallback_uses_rename_destination(tmp_path: Path) -> None:
    repository = _git_repo(tmp_path / "repo")
    subprocess.run(
        ["git", "-C", str(repository), "mv", "app.py", "renamed.py"],
        check=True,
    )

    changes = canonical_repository_changes(repository)

    assert [change.path for change in changes] == ["renamed.py"]


class _PolicyClient:
    def __init__(self, action: str, reason: str) -> None:
        self.action = action
        self.reason = reason

    def send(self, event: ControlEvent) -> PolicyDecision:
        return PolicyDecision(
            decision_id="decision",
            action=self.action,
            reason=self.reason,
            target=ActionTarget(
                kind=TargetKind.SESSION,
                target_id=event.session.session_id,
            ),
            state_version=1,
            state_hash="state",
        )


class _Reader:
    def __init__(self, run: VerificationRun) -> None:
        self.run = run

    def read(self, run_id: str) -> VerificationRun:
        assert run_id == self.run.run_id
        return self.run


class _FailingReader:
    def read(self, run_id: str) -> VerificationRun:
        raise KeyError(run_id)


def _run(verdict: VerificationVerdict) -> VerificationRun:
    contract = _contract("prompt", "placeholder")
    return VerificationRun(
        run_id="verification-run",
        contract=contract,
        status=RunStatus.COMPLETED,
        verdict=verdict,
        created_at=NOW,
        updated_at=NOW,
    )


def _core_decision() -> PolicyDecision:
    return PolicyDecision(
        decision_id="core",
        action="allow",
        reason="core allowed",
        target=ActionTarget(kind=TargetKind.SESSION, target_id="session"),
        state_version=7,
        state_hash="core-state",
    )


def _stop_event(event_id: str) -> ControlEvent:
    return ControlEvent(
        event_id=event_id,
        kind=EventKind.TURN_COMPLETED,
        source="test",
        session=SessionRef(
            host_id="host",
            repo_id="repo",
            session_id="session",
            worktree_id="worktree",
        ),
        payload={"hook_name": "Stop"},
        created_at=NOW,
    )


def _prompt_event(prompt: str) -> ControlEvent:
    return ControlEvent(
        event_id="prompt",
        kind=EventKind.PROMPT_SUBMITTED,
        source="test",
        session=SessionRef(
            host_id="host",
            repo_id="repo",
            session_id="session",
            worktree_id="worktree",
        ),
        payload={"input_text": prompt},
        created_at=NOW,
    )


def _mutation_event(event_id: str) -> ControlEvent:
    return ControlEvent(
        event_id=event_id,
        kind=EventKind.TOOL_CALL,
        source="test",
        session=SessionRef(
            host_id="host",
            repo_id="repo",
            session_id="session",
            worktree_id="worktree",
        ),
        payload={"tool_name": "apply_patch", "arguments": {"patch": "bounded"}},
        created_at=NOW,
    )


def _contract(event_id: str, prompt: str) -> ProofContract:
    return ProofContract(
        task_id="task",
        source_event_id=event_id,
        source_prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        source=ContractSource.CONTRACT_EVENT,
        source_hash="b" * 64,
        acceptance_state=AcceptanceState.CONFIRMED,
        acceptance=["Feature works"],
        checks=[CheckSpec(id="unit", command=["python", "-m", "pytest"])],
    )


class _PersistedBaselineCapture:
    def __init__(self, verification: VerificationService) -> None:
        self.verification = verification
        self.run_id: str | None = None

    async def capture(
        self,
        contract: ProofContract,
        *,
        repository: Path,
        owning_session_id: str,
    ) -> Baseline:
        assert self.run_id is not None
        snapshot = capture_repository_snapshot(repository)
        started = datetime.now(timezone.utc)
        result = CheckResult(
            check_id="unit",
            status=CheckStatus.PASSED,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            exit_code=0,
            isolation=IsolationLevel.SANDBOXED,
            worktree_hash=snapshot.worktree_hash,
        )
        persisted = self.verification.record_baseline_execution(
            self.run_id,
            CheckExecution(
                result=result,
                stdout=b"baseline passed",
                stderr=b"",
                output_artifact_id="baseline-output",
            ),
        )
        return Baseline(
            baseline_id="baseline",
            captured_at=started,
            repository_sha=snapshot.repository_sha,
            worktree_hash=snapshot.worktree_hash,
            dirty_manifest=snapshot.dirty_manifest,
            untracked_manifest=snapshot.untracked_manifest,
            owning_session_id=owning_session_id,
            source_prompt_event_id=contract.source_event_id,
            captured_before_first_mutation=True,
            results=[persisted],
        )


class _UnusedBaselineCapture:
    async def capture(self, *args, **kwargs) -> Baseline:
        raise AssertionError("late attachment must not capture a baseline")


class _FailingBaselineCapture:
    async def capture(self, *args, **kwargs) -> Baseline:
        raise RuntimeError("expected baseline failure")


class _CurrentRunner:
    def __init__(self, repository: Path, failures: list[str] | None = None) -> None:
        self.repository = repository
        self.failures = failures or []

    async def run(
        self,
        spec: CheckSpec,
        *,
        authorization: ExecutionAuthorization,
    ) -> CheckExecution:
        assert authorization.approved is True
        snapshot = capture_repository_snapshot(self.repository)
        started = datetime.now(timezone.utc)
        result = CheckResult(
            check_id=spec.id,
            status=CheckStatus.FAILED if self.failures else CheckStatus.PASSED,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            exit_code=1 if self.failures else 0,
            failure_ids=self.failures,
            isolation=IsolationLevel.SANDBOXED,
            worktree_hash=snapshot.worktree_hash,
        )
        return CheckExecution(
            result=result,
            stdout=b"current verification output",
            stderr=b"",
            output_artifact_id="current-output",
        )


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    (path / "app.py").write_text("GREETING = 'baseline'\n")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=LoopGuard Tests",
            "-c",
            "user.email=tests@loopguard.invalid",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ],
        check=True,
    )
    return path.resolve()
