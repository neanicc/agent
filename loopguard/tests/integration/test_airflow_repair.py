from __future__ import annotations

import json
from pathlib import Path

from loopguard.heal.fixtures import FixtureBuilder
from loopguard.heal.intake import normalize_failure
from loopguard.heal.models import (
    CandidateEvaluation,
    CandidatePatch,
    DataContractDelta,
    RepairRun,
    RepairState,
)
from loopguard.heal.rank import rank_candidates
from loopguard.heal.report import RepairReport, render_report


EVENT = (
    Path(__file__).parents[1]
    / "fixtures"
    / "heal"
    / "events"
    / "airflow_coordinate_failure.json"
)


def test_airflow_failure_reaches_a_redacted_verified_publication_gate() -> None:
    failure = normalize_failure("airflow", json.loads(EVENT.read_text()))
    fixture = FixtureBuilder(key=b"deterministic-test-key").build(
        [
            {
                "email": "developer@example.com",
                "latitude": "43.6532",
                "longitude": -79.3832,
                "token": "ghp_abcdefghijklmnopqrstuvwxyz123456",
            }
        ]
    )
    candidate = CandidatePatch(
        candidate_id="candidate-coordinate-boundary",
        base_sha=failure.revision,
        patch_artifact_id="artifact-patch",
        patch_sha256="b" * 64,
        changed_files=("src/coordinates.py",),
        changed_lines=8,
        strategy="normalize_coordinate_ingestion_boundary",
    )
    evaluation = CandidateEvaluation(
        candidate_id=candidate.candidate_id,
        replay_passed=True,
        regression_passed=True,
        security_passed=True,
        contract_delta=DataContractDelta(),
        evidence_artifact_ids=("artifact-replay", "artifact-regression"),
        changed_files=1,
        changed_lines=8,
    )
    ranking = rank_candidates([evaluation])
    run = RepairRun(
        repair_id="repair-airflow-coordinate",
        failure=failure,
        state=RepairState.RANKED,
        reproduced=True,
        reproduction_artifact_id="artifact-reproduction",
        candidates=(candidate,),
        evaluations=(evaluation,),
        winning_candidate_id=ranking.winner_id,
    ).transition(RepairState.AWAITING_PUBLICATION)
    report = render_report(
        RepairReport(
            repair_id=run.repair_id,
            failure=failure,
            reproduction_command=("pytest", "-q", "tests/test_coordinates.py"),
            reproduction_artifact_id="artifact-reproduction",
            candidates=(candidate,),
            evaluations=(evaluation,),
            ranking=ranking,
            winning_candidate_id=candidate.candidate_id,
            rollback="Revert the draft repair commit and rerun the original Airflow task.",
        )
    )

    assert run.state is RepairState.AWAITING_PUBLICATION
    assert fixture.provenance == "source_redacted"
    assert "developer@example.com" not in fixture.model_dump_json()
    assert "ghp_" not in fixture.model_dump_json()
    assert failure.fingerprint in report
    assert "candidate-coordinate-boundary" in report
