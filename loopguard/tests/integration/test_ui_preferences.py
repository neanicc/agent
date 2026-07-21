from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from loopguard.context.digest import DigestBudget, DigestBuilder
from loopguard.control.daemon import DaemonServices
from loopguard.preferences.models import PreferenceProfile, PreferenceRule, PreferenceVerdict
from loopguard.preferences.service import PreferenceService
from loopguard.preferences.store import PreferenceActor
from loopguard.verify.manifest import ArtifactContext
from loopguard.verify.models import (
    AcceptanceState,
    Baseline,
    CheckResult,
    CheckSpec,
    CheckStatus,
    ContractSource,
    IsolationLevel,
    ProofContract,
    RetentionClass,
    VerdictStatus,
)
from loopguard.verify.service import VerificationService


NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)


def test_soft_design_violation_does_not_fail_verification(tmp_path: Path) -> None:
    completed, preferences, verdict = _complete_with_preference(tmp_path, severity="warn")
    assert completed.verdict.status == VerdictStatus.VERIFIED
    assert completed.verdict.preference_warning_ids == [verdict.verdict_id]
    assert completed.verdict.preference_verdict_ids == [verdict.verdict_id]
    assert preferences.verdict(verdict.verdict_id) == verdict


def test_hard_accessibility_violation_fails_verification(tmp_path: Path) -> None:
    completed, _preferences, verdict = _complete_with_preference(tmp_path, severity="block")
    assert completed.verdict.status == VerdictStatus.INCOMPLETE
    assert completed.verdict.missing_required_checks == ["preference:accessible-name"]
    assert completed.verdict.preference_verdict_ids == [verdict.verdict_id]


def test_authenticated_override_preserves_original_verdict(tmp_path: Path) -> None:
    preferences, verification, run_id, verdict = _prepared(tmp_path, severity="block")
    override = preferences.override(
        verdict.verdict_id,
        actor=PreferenceActor(actor_id="reviewer", authentication="oidc"),
        reason="Known false positive in this captured fixture",
        scope="run",
        scope_id=run_id,
    )
    completed = verification.complete(
        run_id, acceptance_evidence_ids=[verdict.artifact_id]
    )
    assert completed.verdict.status == VerdictStatus.VERIFIED
    assert completed.verdict.preference_override_ids == [override.override_id]
    assert preferences.verdict(verdict.verdict_id).severity == "block"


def test_inflight_run_keeps_exact_profile_across_change_and_restart(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    config = repo / ".loopguard"
    config.mkdir(parents=True)
    (config / "preferences.toml").write_text(
        'schema_version=1\n[[rules]]\nid="density"\nsource="repository"\n'
        'severity="inform"\nstatement="Comfortable"\n'
    )
    preference_path = tmp_path / "preference-policy.db"
    verify_path = tmp_path / "verification.db"
    preferences = PreferenceService.for_path(preference_path)
    first = preferences.compile_profile(repo=repo, cache_scope="repo")
    verification = VerificationService.for_path(verify_path, preferences=preferences)
    run_id = verification.start(
        _contract(), repository_id="repo", preference_profile_id=first.profile_id
    )
    (config / "preferences.toml").write_text(
        'schema_version=1\n[[rules]]\nid="density"\nsource="repository"\n'
        'severity="warn"\nstatement="Compact"\n'
    )
    preferences.invalidate("repo")
    second = preferences.compile_profile(repo=repo, cache_scope="repo")
    assert second.profile_id != first.profile_id
    verification.close()
    preferences.close()

    restarted_preferences = PreferenceService.for_path(preference_path)
    restarted_verification = VerificationService.for_path(
        verify_path, preferences=restarted_preferences
    )
    restarted_run = restarted_verification.read(run_id)
    assert restarted_run.preference_profile_id == first.profile_id
    assert restarted_preferences.profile(first.profile_id) == first
    assert restarted_preferences.cached_profile_id("repo") == second.profile_id


def test_warning_summaries_are_bounded_in_context_digest(tmp_path: Path) -> None:
    _completed, preferences, verdict = _complete_with_preference(tmp_path, severity="warn")
    digest = DigestBuilder().build(
        [],
        since=0,
        budget=DigestBudget(max_chars=2_000),
        preference_warnings=preferences.digest_warnings([verdict.verdict_id]),
    )
    assert verdict.verdict_id in digest.text
    assert "Spacing differs" in digest.text


def test_daemon_services_register_preference_pipeline(tmp_path: Path) -> None:
    preferences = PreferenceService.for_path(tmp_path / "preference-policy.db")
    services = DaemonServices(
        preference_service=preferences,
        preference_compiler=preferences.compiler,
        preference_store=preferences,
        preference_learner=object(),
        preference_evaluators=object(),
        visual_critic=None,
    )
    assert services.preference_service is preferences
    assert services.preference_compiler is preferences.compiler


def _complete_with_preference(tmp_path: Path, *, severity: str):
    preferences, verification, run_id, verdict = _prepared(tmp_path, severity=severity)
    return (
        verification.complete(
            run_id, acceptance_evidence_ids=[verdict.artifact_id]
        ),
        preferences,
        verdict,
    )


def _prepared(tmp_path: Path, *, severity: str):
    preferences = PreferenceService.for_path(tmp_path / "preference-policy.db")
    profile = PreferenceProfile(
        profile_id="profile-test",
        rules=[
            PreferenceRule(
                id="accessible-name",
                source="explicit",
                severity=severity,
                statement="Controls need accessible names",
            )
        ],
    )
    preferences.register_profile(profile, cache_scope="repo")
    verification = VerificationService.for_path(
        tmp_path / "verification.db", preferences=preferences
    )
    run_id = verification.start(
        _contract(), repository_id="repo", preference_profile_id=profile.profile_id
    )
    verification.begin_baselining(run_id)
    baseline_artifact = verification.add_artifact(
        run_id, b"baseline", _artifact_context(run_id, "baseline"), now=NOW
    )
    verification.record_baseline(run_id, _baseline(_result(baseline_artifact.artifact_id, NOW)))
    current_artifact = verification.add_artifact(
        run_id, b"screenshot", _artifact_context(run_id, "current"), now=NOW
    )
    verification.add_result(
        run_id, _result(current_artifact.artifact_id, NOW + timedelta(seconds=1))
    )
    verdict = PreferenceVerdict(
        verdict_id=f"verdict-{severity}",
        rule_id="accessible-name",
        artifact_id=current_artifact.artifact_id,
        evaluator="axe-accessibility",
        evaluator_version="1.0.0",
        status="violation",
        severity=severity,
        summary="Spacing differs" if severity == "warn" else "Accessible name is missing",
        confidence=1,
    )
    verification.add_preference_verdict(run_id, verdict)
    verification.begin_completion(run_id)
    return preferences, verification, run_id, verdict


def _contract() -> ProofContract:
    return ProofContract(
        task_id="task-ui",
        source_event_id="prompt-ui",
        source_prompt_hash="a" * 64,
        source=ContractSource.CONTRACT_EVENT,
        source_hash="b" * 64,
        acceptance_state=AcceptanceState.CONFIRMED,
        acceptance=["UI remains correct"],
        checks=[CheckSpec(id="ui", command=["pytest", "-q"])],
    )


def _result(artifact_id: str, now: datetime) -> CheckResult:
    return CheckResult(
        check_id="ui",
        status=CheckStatus.PASSED,
        started_at=now,
        completed_at=now,
        exit_code=0,
        artifact_ids=[artifact_id],
        isolation=IsolationLevel.SANDBOXED,
        worktree_hash="c" * 64,
    )


def _baseline(result: CheckResult) -> Baseline:
    return Baseline(
        baseline_id="baseline-ui",
        captured_at=NOW,
        repository_sha="d" * 40,
        worktree_hash="c" * 64,
        owning_session_id="session-ui",
        source_prompt_event_id="prompt-ui",
        captured_before_first_mutation=True,
        results=[result],
    )


def _artifact_context(run_id: str, result_id: str) -> ArtifactContext:
    return ArtifactContext(
        repository_id="repo",
        worktree_hash="c" * 64,
        verification_id=run_id,
        command_id="ui",
        result_id=result_id,
        media_type="image/png",
        retention_class=RetentionClass.SCREENSHOTS_TRACES,
    )
