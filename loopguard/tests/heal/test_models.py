from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from loopguard.heal.models import (
    CandidateEvaluation,
    CandidatePatch,
    DataContractDelta,
    FailureEvent,
    PublicationRecord,
    RepairRun,
    RepairState,
)


def test_repair_cannot_evaluate_before_reproduction() -> None:
    run = RepairRun.fixture(state=RepairState.INTAKE)

    with pytest.raises(ValueError, match="reproduction required"):
        run.transition(RepairState.EVALUATING)


def test_publication_requires_verified_candidate() -> None:
    run = RepairRun.fixture(state=RepairState.RANKED, candidates=[])

    with pytest.raises(ValueError, match="verified candidate required"):
        run.transition(RepairState.PUBLISHING)


def test_verified_repair_follows_explicit_state_machine() -> None:
    candidate = CandidatePatch(
        candidate_id="candidate-1",
        base_sha="a" * 40,
        patch_artifact_id="artifact-patch",
        patch_sha256="b" * 64,
        changed_files=("src/ingest.py",),
        changed_lines=8,
    )
    evaluation = CandidateEvaluation(
        candidate_id=candidate.candidate_id,
        replay_passed=True,
        regression_passed=True,
        security_passed=True,
        required_inconclusive=False,
        contract_delta=DataContractDelta(),
        evidence_artifact_ids=("artifact-evaluation",),
    )
    run = RepairRun.fixture(
        state=RepairState.RANKED,
        candidates=[candidate],
        evaluations=[evaluation],
        winning_candidate_id=candidate.candidate_id,
        reproduced=True,
        reproduction_artifact_id="artifact-reproduction",
    )

    awaiting = run.transition(RepairState.AWAITING_PUBLICATION)
    publishing = awaiting.transition(RepairState.PUBLISHING)
    completed = publishing.transition(
        RepairState.COMPLETED,
        publication=PublicationRecord(
            repository_id="repo-1",
            installation_id=42,
            branch="loopguard/repair/repair-1",
            base_sha="a" * 40,
            head_sha="c" * 40,
            patch_sha256=candidate.patch_sha256,
            pull_request_number=7,
            pull_request_url="https://github.com/loopguard/example/pull/7",
            published_at=datetime.now(UTC),
        ),
    )

    assert run.state is RepairState.RANKED
    assert completed.state is RepairState.COMPLETED
    assert completed.publication is not None


def test_models_are_immutable_and_reject_unbounded_failure_text() -> None:
    event = FailureEvent.fixture()

    with pytest.raises(ValidationError, match="frozen"):
        event.message = "changed"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        FailureEvent.fixture(message="x" * 16_385)


def test_completed_state_requires_publication_record() -> None:
    run = RepairRun.fixture(
        state=RepairState.PUBLISHING,
        reproduced=True,
        reproduction_artifact_id="artifact-reproduction",
    )

    with pytest.raises(ValueError, match="publication record required"):
        run.transition(RepairState.COMPLETED)
