from __future__ import annotations

from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopguard.heal.candidates import FileCandidateArtifactStore
from loopguard.heal.github import (
    GitHubPublisher,
    GitHubRepository,
    InMemoryPublicationStore,
    PublicationConflict,
    PublicationRequest,
    PullRequestResult,
    ReverificationRequired,
    ResponseLost,
    TokenExpired,
)
from loopguard.heal.models import (
    CandidateEvaluation,
    CandidatePatch,
    DataContractDelta,
    FailureEvent,
)
from loopguard.heal.rank import RankingResult
from loopguard.heal.report import RepairReport, render_report


class FakeGitHub:
    def __init__(self) -> None:
        self.repository = GitHubRepository(
            repository_id="repo-1",
            installation_id=42,
            owner="loopguard",
            name="coordinates",
            default_branch="main",
            default_branch_sha="a" * 40,
            web_url="https://github.com/loopguard/coordinates",
            permissions=frozenset({"contents", "pull_requests"}),
        )
        self.refs: dict[str, str] = {}
        self.pull_requests: list[dict[str, object]] = []
        self.merges: list[object] = []
        self.deployments: list[object] = []
        self.commits: list[dict[str, object]] = []
        self.lose_ref_response = False
        self.lose_pr_response = False
        self.lose_commit_response = False
        self.expire_commit_token = False
        self.token_refreshes = 0

    def refresh_installation_token(self, installation_id: int) -> None:
        assert installation_id == 42
        self.token_refreshes += 1

    def get_repository(self, installation_id: int, repository_id: str) -> GitHubRepository:
        assert installation_id == 42
        assert repository_id == "repo-1"
        return self.repository

    def get_ref(self, repository_id: str, branch: str) -> str | None:
        assert repository_id == "repo-1"
        return self.refs.get(branch)

    def create_commit_from_patch(
        self,
        *,
        repository_id: str,
        base_sha: str,
        patch: bytes,
        message: str,
        idempotency_key: str,
    ) -> str:
        if self.expire_commit_token:
            self.expire_commit_token = False
            raise TokenExpired("installation token expired")
        item = {
            "repository_id": repository_id,
            "base_sha": base_sha,
            "patch": patch,
            "message": message,
            "idempotency_key": idempotency_key,
        }
        self.commits.append(item)
        if self.lose_commit_response:
            self.lose_commit_response = False
            raise ResponseLost("commit response lost")
        return "c" * 40

    def find_commit_by_idempotency_key(
        self, *, repository_id: str, idempotency_key: str
    ) -> str | None:
        assert repository_id == "repo-1"
        return (
            "c" * 40
            if any(item["idempotency_key"] == idempotency_key for item in self.commits)
            else None
        )

    def create_ref(
        self,
        *,
        repository_id: str,
        branch: str,
        new_sha: str,
        expected_old_sha: str | None,
    ) -> None:
        assert repository_id == "repo-1"
        assert expected_old_sha is None
        if branch in self.refs:
            raise PublicationConflict("ref already exists")
        self.refs[branch] = new_sha
        if self.lose_ref_response:
            self.lose_ref_response = False
            raise ResponseLost("push response lost")

    def find_draft_pull_request(
        self, *, repository_id: str, branch: str
    ) -> PullRequestResult | None:
        for item in self.pull_requests:
            if item["head"] == branch:
                return PullRequestResult(
                    number=int(item["number"]),
                    url=str(item["url"]),
                    draft=bool(item["draft"]),
                    head_sha=str(item["head_sha"]),
                )
        return None

    def create_draft_pull_request(
        self,
        *,
        repository_id: str,
        base_branch: str,
        head_branch: str,
        title: str,
        body: str,
        idempotency_key: str,
    ) -> PullRequestResult:
        assert repository_id == "repo-1"
        item = {
            "number": 7,
            "url": "https://github.com/loopguard/coordinates/pull/7",
            "draft": True,
            "base": base_branch,
            "head": head_branch,
            "head_sha": self.refs[head_branch],
            "title": title,
            "body": body,
            "idempotency_key": idempotency_key,
        }
        self.pull_requests.append(item)
        result = PullRequestResult(
            number=7,
            url=str(item["url"]),
            draft=True,
            head_sha=str(item["head_sha"]),
        )
        if self.lose_pr_response:
            self.lose_pr_response = False
            raise ResponseLost("PR response lost")
        return result


def _verified_request(tmp_path: Path) -> tuple[PublicationRequest, FileCandidateArtifactStore]:
    artifacts = FileCandidateArtifactStore(tmp_path / "artifacts")
    patch = b"""\
diff --git a/pipeline.py b/pipeline.py
--- a/pipeline.py
+++ b/pipeline.py
@@ -1 +1 @@
-VALUE = 'broken'
+VALUE = 'fixed'
"""
    patch_id = artifacts.put_patch(patch)
    candidate = CandidatePatch(
        candidate_id="candidate-boundary",
        base_sha="a" * 40,
        patch_artifact_id=patch_id,
        patch_sha256=patch_id.removeprefix("sha256:"),
        changed_files=("pipeline.py",),
        changed_lines=2,
        strategy="normalize_ingestion_boundary",
    )
    evaluation = CandidateEvaluation(
        candidate_id=candidate.candidate_id,
        replay_passed=True,
        regression_passed=True,
        security_passed=True,
        contract_delta=DataContractDelta(),
        evidence_artifact_ids=("artifact-evaluation",),
        changed_files=1,
        changed_lines=2,
        verification_duration_ms=120,
    )
    report = RepairReport(
        repair_id="repair-1",
        failure=FailureEvent.fixture(fingerprint="f" * 64),
        reproduction_command=("python", "pipeline.py"),
        reproduction_artifact_id="artifact-reproduction",
        candidates=(candidate,),
        evaluations=(evaluation,),
        ranking=RankingResult(
            winner_id=candidate.candidate_id,
            ordered=(candidate.candidate_id,),
            rationale="boundary fix has the least verified risk",
        ),
        winning_candidate_id=candidate.candidate_id,
        rollback="Revert the single repair commit and rerun the original pipeline.",
    )
    return (
        PublicationRequest(
            tenant_id="tenant-1",
            repair_id="repair-1",
            repository_id="repo-1",
            installation_id=42,
            expected_base_sha="a" * 40,
            expected_base_branch="main",
            candidate=candidate,
            evaluation=evaluation,
            report=report,
        ),
        artifacts,
    )


def test_publisher_always_creates_draft_and_never_merges(tmp_path: Path) -> None:
    request, artifacts = _verified_request(tmp_path)
    github = FakeGitHub()

    result = GitHubPublisher(
        github=github,
        publications=InMemoryPublicationStore(),
        patches=artifacts,
    ).publish(request)

    assert github.pull_requests[0]["draft"] is True
    assert github.merges == []
    assert github.deployments == []
    assert result.base_sha == "a" * 40
    assert result.head_sha == "c" * 40
    assert result.patch_sha256 == request.candidate.patch_sha256


def test_report_contains_reproduction_candidates_contract_and_rollback(tmp_path: Path) -> None:
    request, _ = _verified_request(tmp_path)

    text = render_report(request.report)

    assert "Original reproduction" in text
    assert "Candidate matrix" in text
    assert "Data contract delta" in text
    assert "Verification evidence" in text
    assert "Rollback" in text
    assert request.report.failure.message not in text
    assert request.report.failure.fingerprint in text
    expected = (Path(__file__).parents[1] / "fixtures" / "heal" / "expected_pr.md").read_text(
        encoding="utf-8"
    )
    assert text == expected


def test_publication_is_idempotent_and_recovers_lost_responses(tmp_path: Path) -> None:
    request, artifacts = _verified_request(tmp_path)
    github = FakeGitHub()
    github.lose_ref_response = True
    github.lose_pr_response = True
    github.lose_commit_response = True
    publisher = GitHubPublisher(
        github=github,
        publications=InMemoryPublicationStore(),
        patches=artifacts,
    )

    first = publisher.publish(request)
    second = publisher.publish(request)

    assert first == second
    assert len(github.refs) == 1
    assert len(github.pull_requests) == 1


def test_concurrent_publication_creates_one_ref_and_pull_request(tmp_path: Path) -> None:
    request, artifacts = _verified_request(tmp_path)
    github = FakeGitHub()
    publisher = GitHubPublisher(
        github=github,
        publications=InMemoryPublicationStore(),
        patches=artifacts,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _index: publisher.publish(request), range(2)))

    assert records[0] == records[1]
    assert len(github.refs) == 1
    assert len(github.pull_requests) == 1


def test_db_commit_loss_reconciles_existing_branch_and_pr(tmp_path: Path) -> None:
    request, artifacts = _verified_request(tmp_path)
    github = FakeGitHub()

    class LoseFirstStore(InMemoryPublicationStore):
        def __init__(self) -> None:
            super().__init__()
            self.lose = True

        def put_if_absent(self, key, record):
            if self.lose:
                self.lose = False
                raise ResponseLost("database commit response lost")
            return super().put_if_absent(key, record)

    publisher = GitHubPublisher(
        github=github,
        publications=LoseFirstStore(),
        patches=artifacts,
    )
    with pytest.raises(ResponseLost):
        publisher.publish(request)

    record = publisher.publish(request)

    assert record.pull_request_number == 7
    assert len(github.refs) == 1
    assert len(github.pull_requests) == 1


def test_expired_installation_token_retries_with_same_idempotency_key(
    tmp_path: Path,
) -> None:
    request, artifacts = _verified_request(tmp_path)
    github = FakeGitHub()
    github.expire_commit_token = True

    GitHubPublisher(
        github=github,
        publications=InMemoryPublicationStore(),
        patches=artifacts,
        sleeper=lambda _seconds: None,
    ).publish(request)

    assert github.token_refreshes == 1
    assert len(github.commits) == 1


def test_base_advance_requires_fresh_verification_and_never_force_pushes(
    tmp_path: Path,
) -> None:
    request, artifacts = _verified_request(tmp_path)
    github = FakeGitHub()
    github.repository = github.repository.model_copy(update={"default_branch_sha": "b" * 40})

    with pytest.raises(ReverificationRequired):
        GitHubPublisher(
            github=github,
            publications=InMemoryPublicationStore(),
            patches=artifacts,
        ).publish(request)

    assert github.commits == []
    assert github.refs == {}


def test_branch_collision_uses_stable_suffix_then_fails_on_unexpected_remote(
    tmp_path: Path,
) -> None:
    request, artifacts = _verified_request(tmp_path)
    github = FakeGitHub()
    publisher = GitHubPublisher(
        github=github,
        publications=InMemoryPublicationStore(),
        patches=artifacts,
    )
    base_branch = publisher.branch_candidates(request)[0]
    github.refs[base_branch] = "d" * 40

    result = publisher.publish(request)

    assert result.branch != base_branch
    assert result.branch.endswith(request.candidate.patch_sha256[:8])

    other_request = request.model_copy(
        update={
            "repair_id": "repair-other",
            "tenant_id": "tenant-other",
            "report": request.report.model_copy(update={"repair_id": "repair-other"}),
        }
    )
    other_branches = publisher.branch_candidates(other_request)
    github.refs[other_branches[0]] = "e" * 40
    github.refs[other_branches[1]] = "f" * 40
    with pytest.raises(PublicationConflict):
        publisher.publish(other_request)


def test_installation_repository_binding_and_verified_winner_are_required(
    tmp_path: Path,
) -> None:
    request, artifacts = _verified_request(tmp_path)
    github = FakeGitHub()
    publisher = GitHubPublisher(
        github=github,
        publications=InMemoryPublicationStore(),
        patches=artifacts,
    )

    github.repository = github.repository.model_copy(update={"installation_id": 99})
    with pytest.raises(PublicationConflict):
        publisher.publish(request)

    github.repository = github.repository.model_copy(update={"installation_id": 42})
    unverified = request.model_copy(
        update={"evaluation": request.evaluation.model_copy(update={"security_passed": False})}
    )
    with pytest.raises(PublicationConflict):
        publisher.publish(unverified)


def test_publication_record_uses_authoritative_renamed_repository_url(tmp_path: Path) -> None:
    request, artifacts = _verified_request(tmp_path)
    github = FakeGitHub()
    github.repository = github.repository.model_copy(
        update={
            "owner": "new-owner",
            "name": "renamed",
            "web_url": "https://github.com/new-owner/renamed",
        }
    )
    # GitHub returns the authoritative URL after a rename/transfer.
    original_create = github.create_draft_pull_request

    def renamed_create(**kwargs):
        result = original_create(**kwargs)
        return result.model_copy(update={"url": "https://github.com/new-owner/renamed/pull/7"})

    github.create_draft_pull_request = renamed_create  # type: ignore[method-assign]
    record = GitHubPublisher(
        github=github,
        publications=InMemoryPublicationStore(),
        patches=artifacts,
    ).publish(request)

    assert str(record.pull_request_url) == "https://github.com/new-owner/renamed/pull/7"
    assert record.published_at <= datetime.now(UTC)
