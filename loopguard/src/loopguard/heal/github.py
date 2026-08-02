"""Idempotent, draft-only GitHub App publication for verified repairs."""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Callable, Iterator, Literal, Protocol, TypeVar

from pydantic import Field, HttpUrl

from loopguard.heal.models import (
    CandidateEvaluation,
    CandidatePatch,
    PublicationRecord,
    RepairModel,
)
from loopguard.heal.report import RepairReport, render_report


class PublicationConflict(RuntimeError):
    pass


class ReverificationRequired(PublicationConflict):
    pass


class ResponseLost(RuntimeError):
    pass


class TokenExpired(RuntimeError):
    pass


class RateLimited(RuntimeError):
    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("GitHub App request was rate limited")


class GitHubRepository(RepairModel):
    repository_id: str = Field(min_length=1, max_length=256)
    installation_id: int = Field(gt=0)
    owner: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    default_branch: str = Field(min_length=1, max_length=255)
    default_branch_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    web_url: HttpUrl
    permissions: frozenset[Literal["contents", "pull_requests"]]


class PullRequestResult(RepairModel):
    number: int = Field(gt=0)
    url: HttpUrl
    draft: bool
    head_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")


class PublicationRequest(RepairModel):
    tenant_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    repair_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    repository_id: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
    installation_id: int = Field(gt=0)
    expected_base_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    expected_base_branch: str = Field(min_length=1, max_length=255)
    candidate: CandidatePatch
    evaluation: CandidateEvaluation
    report: RepairReport

    def idempotency_key(self) -> str:
        return hashlib.sha256(
            "\0".join(
                (
                    self.tenant_id,
                    self.repository_id,
                    self.repair_id,
                    self.candidate.patch_sha256,
                )
            ).encode()
        ).hexdigest()


class GitHubPublicationClient(Protocol):
    def refresh_installation_token(self, installation_id: int) -> None: ...

    def get_repository(self, installation_id: int, repository_id: str) -> GitHubRepository: ...

    def get_ref(self, repository_id: str, branch: str) -> str | None: ...

    def create_commit_from_patch(
        self,
        *,
        repository_id: str,
        base_sha: str,
        patch: bytes,
        message: str,
        idempotency_key: str,
    ) -> str: ...

    def find_commit_by_idempotency_key(
        self, *, repository_id: str, idempotency_key: str
    ) -> str | None: ...

    def create_ref(
        self,
        *,
        repository_id: str,
        branch: str,
        new_sha: str,
        expected_old_sha: str | None,
    ) -> None: ...

    def find_draft_pull_request(
        self, *, repository_id: str, branch: str
    ) -> PullRequestResult | None: ...

    def create_draft_pull_request(
        self,
        *,
        repository_id: str,
        base_branch: str,
        head_branch: str,
        title: str,
        body: str,
        idempotency_key: str,
    ) -> PullRequestResult: ...


class PatchReader(Protocol):
    def read_patch(self, artifact_id: str, *, maximum_bytes: int) -> bytes: ...


class PublicationStore(Protocol):
    def locked(self, key: str) -> AbstractContextManager[None]: ...

    def get(self, key: str) -> PublicationRecord | None: ...

    def put_if_absent(self, key: str, record: PublicationRecord) -> PublicationRecord: ...


class InMemoryPublicationStore:
    def __init__(self) -> None:
        self._records: dict[str, PublicationRecord] = {}
        self._lock = threading.RLock()

    @contextmanager
    def locked(self, key: str) -> Iterator[None]:
        del key
        with self._lock:
            yield

    def get(self, key: str) -> PublicationRecord | None:
        return self._records.get(key)

    def put_if_absent(self, key: str, record: PublicationRecord) -> PublicationRecord:
        existing = self._records.setdefault(key, record)
        if (
            existing.repository_id != record.repository_id
            or existing.patch_sha256 != record.patch_sha256
        ):
            raise PublicationConflict("publication idempotency record conflicts")
        return existing


class GitHubPublisher:
    """Publish one verified patch as a draft PR; this type has no merge/deploy API."""

    def __init__(
        self,
        *,
        github: GitHubPublicationClient,
        publications: PublicationStore,
        patches: PatchReader,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.github = github
        self.publications = publications
        self.patches = patches
        self.sleeper = sleeper

    def branch_candidates(self, request: PublicationRequest) -> tuple[str, str]:
        repair_hash = hashlib.sha256(
            f"{request.tenant_id}:{request.repository_id}:{request.repair_id}".encode()
        ).hexdigest()[:12]
        base = f"loopguard/repair/{repair_hash}"
        return base, f"{base}-{request.candidate.patch_sha256[:8]}"

    def publish(self, request: PublicationRequest) -> PublicationRecord:
        self._validate_request(request)
        key = request.idempotency_key()
        with self.publications.locked(key):
            existing = self.publications.get(key)
            if existing is not None:
                return existing
            repository = self._read(
                request.installation_id,
                lambda: self.github.get_repository(
                    request.installation_id,
                    request.repository_id,
                ),
            )
            self._validate_repository(request, repository)
            try:
                patch = self.patches.read_patch(
                    request.candidate.patch_artifact_id,
                    maximum_bytes=64 * 1024 * 1024,
                )
            except (OSError, ValueError) as exc:
                raise PublicationConflict("winning patch artifact is unavailable") from exc
            if hashlib.sha256(patch).hexdigest() != request.candidate.patch_sha256:
                raise PublicationConflict("winning patch artifact failed integrity verification")

            branches = self.branch_candidates(request)
            observed = {
                branch: self._read(
                    request.installation_id,
                    lambda branch=branch: self.github.get_ref(request.repository_id, branch),
                )
                for branch in branches
            }
            head_sha = self._create_or_reconcile_commit(request, patch, key)
            if not _git_sha(head_sha):
                raise PublicationConflict("GitHub returned an invalid commit object ID")
            branch = self._select_branch(branches, observed, head_sha)
            if observed[branch] is None:
                self._create_or_reconcile_ref(request, branch, head_sha)

            pull_request = self._read(
                request.installation_id,
                lambda: self.github.find_draft_pull_request(
                    repository_id=request.repository_id,
                    branch=branch,
                ),
            )
            if pull_request is None:
                pull_request = self._create_or_reconcile_pr(
                    request,
                    repository,
                    branch,
                    head_sha,
                    key,
                )
            self._validate_pull_request(pull_request, head_sha, repository)
            record = PublicationRecord(
                repository_id=request.repository_id,
                installation_id=request.installation_id,
                branch=branch,
                base_sha=request.expected_base_sha,
                head_sha=head_sha,
                patch_sha256=request.candidate.patch_sha256,
                pull_request_number=pull_request.number,
                pull_request_url=pull_request.url,
                published_at=datetime.now(UTC),
            )
            return self.publications.put_if_absent(key, record)

    def _validate_request(self, request: PublicationRequest) -> None:
        if (
            request.candidate.candidate_id != request.evaluation.candidate_id
            or not request.evaluation.verified
            or request.report.repair_id != request.repair_id
            or request.report.winning_candidate_id != request.candidate.candidate_id
            or request.report.ranking.winner_id != request.candidate.candidate_id
            or request.candidate.base_sha != request.expected_base_sha
        ):
            raise PublicationConflict("publication requires the matching verified winner")

    def _validate_repository(
        self,
        request: PublicationRequest,
        repository: GitHubRepository,
    ) -> None:
        if (
            repository.repository_id != request.repository_id
            or repository.installation_id != request.installation_id
            or repository.permissions != frozenset({"contents", "pull_requests"})
        ):
            raise PublicationConflict("GitHub installation/repository binding changed")
        if (
            repository.default_branch != request.expected_base_branch
            or repository.default_branch_sha != request.expected_base_sha
        ):
            raise ReverificationRequired(
                "base branch advanced or changed; reapply and fully reverify before publication"
            )

    def _select_branch(
        self,
        branches: tuple[str, str],
        observed: dict[str, str | None],
        head_sha: str,
    ) -> str:
        for branch in branches:
            if observed[branch] in {None, head_sha}:
                return branch
        raise PublicationConflict("repair branches contain unexpected remote commits")

    def _create_or_reconcile_ref(
        self,
        request: PublicationRequest,
        branch: str,
        head_sha: str,
    ) -> None:
        for attempt in range(3):
            try:
                self.github.create_ref(
                    repository_id=request.repository_id,
                    branch=branch,
                    new_sha=head_sha,
                    expected_old_sha=None,
                )
                return
            except (ResponseLost, PublicationConflict, TokenExpired, RateLimited) as exc:
                observed = self._read(
                    request.installation_id,
                    lambda: self.github.get_ref(request.repository_id, branch),
                )
                if observed == head_sha:
                    return
                if isinstance(exc, PublicationConflict) or attempt == 2:
                    raise PublicationConflict(
                        "repair ref creation could not be reconciled"
                    ) from None
                self._recover_retry(request.installation_id, exc)

    def _create_or_reconcile_pr(
        self,
        request: PublicationRequest,
        repository: GitHubRepository,
        branch: str,
        head_sha: str,
        key: str,
    ) -> PullRequestResult:
        for attempt in range(3):
            try:
                return self.github.create_draft_pull_request(
                    repository_id=request.repository_id,
                    base_branch=repository.default_branch,
                    head_branch=branch,
                    title=f"LoopGuard: verified pipeline repair {key[:12]}",
                    body=render_report(request.report),
                    idempotency_key=f"{key}:pull-request",
                )
            except (ResponseLost, TokenExpired, RateLimited) as exc:
                result = self._read(
                    request.installation_id,
                    lambda: self.github.find_draft_pull_request(
                        repository_id=request.repository_id,
                        branch=branch,
                    ),
                )
                if result is not None:
                    if result.head_sha != head_sha:
                        raise PublicationConflict("reconciled pull request has an unexpected head")
                    return result
                if attempt == 2:
                    raise PublicationConflict(
                        "draft pull request creation could not be reconciled"
                    ) from None
                self._recover_retry(request.installation_id, exc)
        raise AssertionError("bounded pull request loop did not terminate")

    def _create_or_reconcile_commit(
        self,
        request: PublicationRequest,
        patch: bytes,
        key: str,
    ) -> str:
        commit_key = f"{key}:commit"
        for attempt in range(3):
            try:
                return self.github.create_commit_from_patch(
                    repository_id=request.repository_id,
                    base_sha=request.expected_base_sha,
                    patch=patch,
                    message=f"fix: repair verified pipeline failure {key[:12]}",
                    idempotency_key=commit_key,
                )
            except (ResponseLost, TokenExpired, RateLimited) as exc:
                reconciled = self._read(
                    request.installation_id,
                    lambda: self.github.find_commit_by_idempotency_key(
                        repository_id=request.repository_id,
                        idempotency_key=commit_key,
                    ),
                )
                if reconciled is not None:
                    return reconciled
                if attempt == 2:
                    raise PublicationConflict(
                        "repair commit creation could not be reconciled"
                    ) from None
                self._recover_retry(request.installation_id, exc)
        raise AssertionError("bounded commit loop did not terminate")

    def _recover_retry(self, installation_id: int, error: BaseException) -> None:
        if isinstance(error, TokenExpired):
            self.github.refresh_installation_token(installation_id)
            return
        if isinstance(error, RateLimited):
            if not 0 <= error.retry_after_seconds <= 2:
                raise PublicationConflict("GitHub rate-limit retry exceeds policy") from error
            self.sleeper(error.retry_after_seconds)

    def _read(self, installation_id: int, operation: Callable[[], _T]) -> _T:
        for attempt in range(3):
            try:
                return operation()
            except (TokenExpired, RateLimited) as exc:
                if attempt == 2:
                    raise PublicationConflict("GitHub read retry budget exhausted") from exc
                self._recover_retry(installation_id, exc)
        raise AssertionError("bounded GitHub read loop did not terminate")

    @staticmethod
    def _validate_pull_request(
        result: PullRequestResult,
        head_sha: str,
        repository: GitHubRepository,
    ) -> None:
        expected_prefix = str(repository.web_url).rstrip("/") + "/pull/"
        if (
            not result.draft
            or result.head_sha != head_sha
            or not str(result.url).startswith(expected_prefix)
        ):
            raise PublicationConflict("publication result is not the expected draft pull request")


def _git_sha(value: str) -> bool:
    return len(value) in {40, 64} and all(character in "0123456789abcdef" for character in value)


_T = TypeVar("_T")
