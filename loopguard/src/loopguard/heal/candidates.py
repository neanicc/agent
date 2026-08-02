"""Independent worktree generation and post-generation patch admission."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import shlex
import stat
import subprocess
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import Field, field_validator

from loopguard.heal.models import CandidatePatch, FailureEvent, RepairModel
from loopguard.heal.planner import HostileEvidenceEnvelope, RepairPlanner, RepairStrategyBrief
from loopguard.heal.sandbox import WorktreeCheckout


class CandidateBudget(RepairModel):
    max_files: int = Field(default=8, ge=1, le=64)
    max_changed_lines: int = Field(default=400, ge=1, le=10_000)
    max_tool_calls: int = Field(default=30, ge=1, le=1_000)
    max_wall_seconds: int = Field(default=600, ge=1, le=3_600)
    max_cost_usd: Decimal = Field(default=Decimal("2.00"), ge=0, le=1_000)
    max_tokens: int = Field(default=100_000, ge=1, le=1_000_000)
    max_patch_bytes: int = Field(default=4 * 1024 * 1024, ge=1_024, le=64 * 1024 * 1024)
    max_context_files: int = Field(default=5_000, ge=1, le=100_000)
    max_context_bytes: int = Field(
        default=128 * 1024 * 1024,
        ge=1_024,
        le=1024 * 1024 * 1024,
    )

    @field_validator("max_cost_usd")
    @classmethod
    def finite_cost(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("candidate cost budget must be finite")
        return value


class PatchPolicy(RepairModel):
    allowed_paths: tuple[str, ...] = Field(min_length=1, max_length=128)
    allow_binary: bool = False
    allow_dependency_locks: bool = False
    allow_ci: bool = False
    allow_secret_paths: bool = False
    allow_infrastructure: bool = False
    allow_migrations: bool = False

    @field_validator("allowed_paths")
    @classmethod
    def safe_patterns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for raw in values:
            value = raw.replace("\\", "/")
            if (
                not value
                or len(value) > 512
                or value.startswith("/")
                or "\x00" in value
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
                or any(part == ".." for part in value.split("/"))
            ):
                raise ValueError("patch allowlist contains an unsafe path pattern")
            normalized.append(value)
        return tuple(dict.fromkeys(normalized))


PatchCode = Literal[
    "accepted",
    "empty_patch",
    "invalid_patch",
    "scope_violation",
    "file_budget_exceeded",
    "line_budget_exceeded",
    "binary_change_forbidden",
    "dependency_change_forbidden",
    "ci_change_forbidden",
    "secret_path_forbidden",
    "infrastructure_change_forbidden",
    "migration_change_forbidden",
    "unsafe_file_type",
]


class PatchAdmission(RepairModel):
    code: PatchCode
    changed_files: tuple[str, ...] = ()
    changed_lines: int = Field(default=0, ge=0, le=10_000_000)
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rejected_paths: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.code == "accepted"


class CandidateSandbox(RepairModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    repository_id: str = Field(min_length=1, max_length=256)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    worktree_root: Path
    allowed_paths: tuple[str, ...] = Field(min_length=1, max_length=128)
    network_enabled: Literal[False] = False
    credentials_mounted: Literal[False] = False
    cloud_apis_enabled: Literal[False] = False
    publishing_enabled: Literal[False] = False
    host_tools_enabled: Literal[False] = False

    @field_validator("worktree_root")
    @classmethod
    def absolute_worktree(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("candidate worktree root must be absolute")
        return value


class CandidateRejection(RepairModel):
    candidate_id: str
    code: str = Field(min_length=1, max_length=128)
    detail: str = Field(min_length=1, max_length=512)


class CandidateGenerationEvidence(RepairModel):
    candidate_id: str
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    effort: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=128)
    tool_calls: int = Field(ge=0, le=1_000_000)
    input_tokens: int = Field(ge=0, le=1_000_000_000)
    output_tokens: int = Field(ge=0, le=1_000_000_000)
    cost_usd: Decimal = Field(ge=0, le=1_000_000)
    stop_reason: str = Field(min_length=1, max_length=128)
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("cost_usd")
    @classmethod
    def finite_cost(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("candidate evidence cost must be finite")
        return value


class CandidateGenerationResult(RepairModel):
    accepted: tuple[CandidatePatch, ...] = ()
    rejected: tuple[CandidateRejection, ...] = ()
    evidence: tuple[CandidateGenerationEvidence, ...] = ()


class CandidateGenerator(Protocol):
    async def generate(
        self,
        brief: RepairStrategyBrief,
        sandbox: CandidateSandbox,
        budget: CandidateBudget,
    ) -> CandidatePatch: ...


class GeneratorEvidenceProvider(Protocol):
    def take_evidence(self, candidate_id: str) -> CandidateGenerationEvidence | None: ...


class CandidateArtifactStore(Protocol):
    def put_patch(self, patch: bytes) -> str: ...

    def put_evidence(self, evidence: CandidateGenerationEvidence) -> str: ...


class FileCandidateArtifactStore:
    """Private content-addressed staging before durable workflow upload."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("candidate artifact root must be a regular directory")
        self.root.chmod(0o700)

    def put_patch(self, patch: bytes) -> str:
        return self._put(patch, suffix=".patch")

    def put_evidence(self, evidence: CandidateGenerationEvidence) -> str:
        payload = json.dumps(
            evidence.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return self._put(payload, suffix=".json")

    def put_blob(self, payload: bytes, *, suffix: str = ".bin") -> str:
        if not suffix.startswith(".") or len(suffix) > 16 or not suffix[1:].isalnum():
            raise ValueError("candidate artifact suffix is invalid")
        return self._put(payload, suffix=suffix)

    def read_patch(self, artifact_id: str, *, maximum_bytes: int = 64 * 1024 * 1024) -> bytes:
        prefix = "sha256:"
        digest = artifact_id.removeprefix(prefix)
        if (
            not artifact_id.startswith(prefix)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("candidate patch artifact identity is invalid")
        path = self.root / f"{digest}.patch"
        details = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(details.st_mode):
            raise ValueError("candidate patch artifact is not a regular file")
        if details.st_size > maximum_bytes:
            raise ValueError("candidate patch artifact exceeds its read budget")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("candidate patch artifact failed integrity verification")
        return payload

    def _put(self, payload: bytes, *, suffix: str) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        path = self.root / f"{digest}{suffix}"
        if path.is_symlink():
            raise RuntimeError("candidate artifact path cannot be a symlink")
        try:
            output = path.open("xb")
        except FileExistsError:
            details = path.lstat()
            if not stat.S_ISREG(details.st_mode):
                raise RuntimeError("candidate artifact path must be a regular file")
            if path.read_bytes() != payload:
                raise RuntimeError("content-addressed candidate artifact collision")
        else:
            with output:
                output.write(payload)
            path.chmod(0o600)
        return f"sha256:{digest}"


class CandidateGenerationService:
    """Generate each candidate from one immutable revision and verify its diff."""

    def __init__(
        self,
        *,
        repository: Path,
        worktree_root: Path,
        generator: CandidateGenerator,
        planner: RepairPlanner | None = None,
        artifact_store: CandidateArtifactStore | None = None,
    ) -> None:
        self.repository = repository.expanduser().resolve(strict=True)
        self.worktree_root = worktree_root.expanduser().absolute()
        if self.worktree_root.is_symlink():
            raise ValueError("candidate worktree root cannot be a symlink")
        if self.worktree_root.is_relative_to(self.repository) or self.repository.is_relative_to(
            self.worktree_root
        ):
            raise ValueError("candidate worktree root and repository must not overlap")
        self.worktree_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.worktree_root.resolve(strict=True) != self.worktree_root:
            raise ValueError("candidate worktree root cannot contain a symlink")
        self.worktree_root.chmod(0o700)
        self.generator = generator
        self.planner = planner or RepairPlanner()
        self.artifact_store = artifact_store or FileCandidateArtifactStore(
            self.worktree_root / "_artifacts"
        )
        reported_root = Path(_git(self.repository, "rev-parse", "--show-toplevel"))
        if reported_root.resolve(strict=True) != self.repository:
            raise ValueError("candidate repository must be the Git worktree root")

    async def generate(
        self,
        *,
        failure: FailureEvent,
        evidence: HostileEvidenceEnvelope,
        policy: PatchPolicy,
        budget: CandidateBudget,
        count: int = 3,
    ) -> CandidateGenerationResult:
        evidence = evidence.model_copy(update={"failure_message": failure.message})
        briefs = self.planner.plan(
            failure=failure,
            evidence=evidence,
            allowed_paths=policy.allowed_paths,
            count=count,
        )
        accepted: list[CandidatePatch] = []
        rejected: list[CandidateRejection] = []
        generation_evidence: list[CandidateGenerationEvidence] = []
        for brief in briefs:
            try:
                with WorktreeCheckout(
                    self.repository,
                    revision=brief.base_sha,
                    root=self.worktree_root,
                    repair_id=brief.candidate_id,
                ) as materialized:
                    sandbox = CandidateSandbox(
                        candidate_id=brief.candidate_id,
                        repository_id=brief.repository_id,
                        base_sha=brief.base_sha,
                        worktree_root=materialized.path,
                        allowed_paths=policy.allowed_paths,
                    )
                    if _git(materialized.path, "status", "--porcelain"):
                        raise RuntimeError("candidate baseline was not pristine")
                    claimed = await asyncio.wait_for(
                        self.generator.generate(brief, sandbox, budget),
                        timeout=budget.max_wall_seconds,
                    )
                    if (
                        claimed.candidate_id != brief.candidate_id
                        or claimed.base_sha != brief.base_sha
                    ):
                        raise CandidateGenerationFailure(
                            "candidate_identity_mismatch",
                            "generator returned a candidate for another baseline",
                        )
                    item = _take_generator_evidence(self.generator, brief.candidate_id)
                    patch = _worktree_patch(
                        materialized.path,
                        maximum_bytes=budget.max_patch_bytes,
                        maximum_files=budget.max_files,
                    )
                    admission = validate_unified_patch(
                        patch,
                        policy=policy,
                        budget=budget,
                    )
                    if not admission.accepted:
                        rejected.append(
                            CandidateRejection(
                                candidate_id=brief.candidate_id,
                                code=admission.code,
                                detail=_admission_detail(admission),
                            )
                        )
                        continue
                    patch_artifact_id = self.artifact_store.put_patch(patch)
                    evidence_artifact_ids: tuple[str, ...] = ()
                    if item is not None:
                        item = item.model_copy(update={"patch_sha256": admission.patch_sha256})
                        evidence_artifact_ids = (self.artifact_store.put_evidence(item),)
                        generation_evidence.append(item)
                    accepted.append(
                        CandidatePatch(
                            candidate_id=brief.candidate_id,
                            base_sha=brief.base_sha,
                            patch_artifact_id=patch_artifact_id,
                            patch_sha256=admission.patch_sha256,
                            changed_files=admission.changed_files,
                            changed_lines=admission.changed_lines,
                            strategy=brief.strategy.value,
                            generator_evidence_artifact_ids=evidence_artifact_ids,
                        )
                    )
            except TimeoutError:
                rejected.append(
                    CandidateRejection(
                        candidate_id=brief.candidate_id,
                        code="generator_timeout",
                        detail="candidate generation exceeded its wall-time budget",
                    )
                )
            except CandidateGenerationFailure as exc:
                rejected.append(
                    CandidateRejection(
                        candidate_id=brief.candidate_id,
                        code=exc.code,
                        detail=str(exc),
                    )
                )
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
                rejected.append(
                    CandidateRejection(
                        candidate_id=brief.candidate_id,
                        code="candidate_generation_failed",
                        detail=str(exc)[:512] or "candidate generation failed",
                    )
                )
        return CandidateGenerationResult(
            accepted=tuple(accepted),
            rejected=tuple(rejected),
            evidence=tuple(generation_evidence),
        )


class CandidateGenerationFailure(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


def validate_unified_patch(
    patch: bytes,
    *,
    policy: PatchPolicy,
    budget: CandidateBudget,
) -> PatchAdmission:
    digest = hashlib.sha256(patch).hexdigest()
    if not patch:
        return PatchAdmission(code="empty_patch", patch_sha256=digest)
    try:
        lines = patch.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return PatchAdmission(code="binary_change_forbidden", patch_sha256=digest)

    paths: list[str] = []
    for line in lines:
        if not line.startswith("diff --git "):
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            return PatchAdmission(code="invalid_patch", patch_sha256=digest)
        if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
            return PatchAdmission(code="invalid_patch", patch_sha256=digest)
        old_path = _safe_patch_path(parts[2][2:])
        new_path = _safe_patch_path(parts[3][2:])
        if old_path is None or new_path is None:
            return PatchAdmission(code="invalid_patch", patch_sha256=digest)
        paths.extend((old_path, new_path))
    changed_files = tuple(dict.fromkeys(paths))
    if not changed_files:
        return PatchAdmission(code="invalid_patch", patch_sha256=digest)

    rejected = tuple(
        path
        for path in changed_files
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in policy.allowed_paths)
    )
    if rejected:
        return PatchAdmission(
            code="scope_violation",
            changed_files=changed_files,
            patch_sha256=digest,
            rejected_paths=rejected,
        )
    if len(changed_files) > budget.max_files:
        return PatchAdmission(
            code="file_budget_exceeded",
            changed_files=changed_files,
            patch_sha256=digest,
        )
    if not policy.allow_binary and (
        b"GIT binary patch" in patch or b"Binary files " in patch or b"\x00" in patch
    ):
        return PatchAdmission(
            code="binary_change_forbidden",
            changed_files=changed_files,
            patch_sha256=digest,
        )
    if any(
        marker in patch
        for marker in (
            b"new file mode 120000",
            b"old mode 120000",
            b"new mode 160000",
            b"old mode 160000",
        )
    ):
        return PatchAdmission(
            code="unsafe_file_type",
            changed_files=changed_files,
            patch_sha256=digest,
        )
    category_checks = (
        (
            policy.allow_dependency_locks,
            "dependency_change_forbidden",
            _is_dependency_lock,
        ),
        (policy.allow_ci, "ci_change_forbidden", _is_ci_path),
        (policy.allow_secret_paths, "secret_path_forbidden", _is_secret_path),
        (
            policy.allow_infrastructure,
            "infrastructure_change_forbidden",
            _is_infrastructure_path,
        ),
        (policy.allow_migrations, "migration_change_forbidden", _is_migration_path),
    )
    for allowed, code, predicate in category_checks:
        denied = tuple(path for path in changed_files if predicate(path))
        if denied and not allowed:
            return PatchAdmission(
                code=code,  # type: ignore[arg-type]
                changed_files=changed_files,
                patch_sha256=digest,
                rejected_paths=denied,
            )

    changed_lines = sum(
        1
        for line in lines
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )
    if changed_lines > budget.max_changed_lines:
        return PatchAdmission(
            code="line_budget_exceeded",
            changed_files=changed_files,
            changed_lines=changed_lines,
            patch_sha256=digest,
        )
    return PatchAdmission(
        code="accepted",
        changed_files=changed_files,
        changed_lines=changed_lines,
        patch_sha256=digest,
    )


def _worktree_patch(
    worktree: Path,
    *,
    maximum_bytes: int,
    maximum_files: int,
) -> bytes:
    process = subprocess.Popen(
        ["git", "diff", "--binary", "--full-index", "--no-ext-diff", "HEAD", "--"],
        cwd=worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    tracked = process.stdout.read(maximum_bytes + 1)
    if len(tracked) > maximum_bytes:
        process.kill()
        process.wait()
        raise CandidateGenerationFailure(
            "patch_byte_budget_exceeded",
            "candidate patch exceeded its byte budget",
        )
    if process.wait() != 0:
        raise CandidateGenerationFailure(
            "patch_inspection_failed",
            "Git could not inspect the candidate patch",
        )
    untracked_process = subprocess.Popen(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert untracked_process.stdout is not None
    untracked_output = untracked_process.stdout.read(maximum_bytes + 1)
    if len(untracked_output) > maximum_bytes:
        untracked_process.kill()
        untracked_process.wait()
        raise CandidateGenerationFailure(
            "file_budget_exceeded",
            "candidate untracked-file index exceeded its budget",
        )
    if untracked_process.wait() != 0:
        raise CandidateGenerationFailure(
            "patch_inspection_failed",
            "Git could not inspect candidate untracked files",
        )
    try:
        untracked = tuple(path.decode("utf-8") for path in untracked_output.split(b"\x00") if path)
    except UnicodeDecodeError as exc:
        raise CandidateGenerationFailure(
            "unsafe_file_type",
            "candidate created a non-UTF-8 path",
        ) from exc
    if len(untracked) > maximum_files:
        raise CandidateGenerationFailure(
            "file_budget_exceeded",
            "candidate created more files than its budget permits",
        )
    chunks = [tracked]
    observed_bytes = len(tracked)
    for relative in sorted(untracked):
        safe = _safe_patch_path(relative)
        if safe is None:
            raise CandidateGenerationFailure(
                "unsafe_file_type",
                "candidate created an unsafe path",
            )
        path = worktree / safe
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or path.is_symlink():
            raise CandidateGenerationFailure(
                "unsafe_file_type",
                f"candidate created a non-regular file: {safe}",
            )
        if observed_bytes + details.st_size > maximum_bytes:
            raise CandidateGenerationFailure(
                "patch_byte_budget_exceeded",
                "candidate patch exceeded its byte budget",
            )
        content = path.read_bytes()
        observed_bytes += len(content)
        if observed_bytes > maximum_bytes:
            raise CandidateGenerationFailure(
                "patch_byte_budget_exceeded",
                "candidate patch exceeded its byte budget",
            )
        old_header = shlex.quote(f"a/{safe}")
        new_header = shlex.quote(f"b/{safe}")
        header = (
            f"diff --git {old_header} {new_header}\nnew file mode 100644\nindex 0000000..0000000\n"
        ).encode()
        if b"\x00" in content:
            chunks.append(header + b"GIT binary patch\n")
            continue
        text = content.decode("utf-8")
        additions = "".join(f"+{line}\n" for line in text.splitlines())
        chunks.append(
            header
            + (
                f"--- /dev/null\n+++ {new_header}\n@@ -0,0 +1,{len(text.splitlines())} @@\n"
            ).encode()
            + additions.encode()
        )
    patch = b"".join(chunks)
    if len(patch) > maximum_bytes:
        raise CandidateGenerationFailure(
            "patch_byte_budget_exceeded",
            "candidate patch exceeded its byte budget",
        )
    return patch


def _safe_patch_path(value: str) -> str | None:
    normalized = value.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or "\x00" in normalized
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        return None
    return candidate.as_posix()


def _is_dependency_lock(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name in {
        "bun.lock",
        "bun.lockb",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }


def _is_ci_path(path: str) -> bool:
    lowered = path.lower()
    return (
        lowered.startswith(".github/workflows/")
        or lowered.startswith(".circleci/")
        or lowered == ".gitlab-ci.yml"
        or lowered.startswith("azure-pipelines")
    )


def _is_secret_path(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return (
        name == ".env"
        or name.startswith(".env.")
        or "credential" in name
        or "secret" in name
        or name.endswith((".key", ".pem", ".p12", ".pfx"))
    )


def _is_infrastructure_path(path: str) -> bool:
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    return (
        lowered.startswith(("infra/", "infrastructure/", "terraform/", "k8s/", "helm/"))
        or name in {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}
        or name.endswith((".tf", ".tfvars"))
    )


def _is_migration_path(path: str) -> bool:
    parts = {part.lower() for part in PurePosixPath(path).parts}
    return bool(parts & {"migration", "migrations"}) or ("alembic" in parts and "versions" in parts)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _take_generator_evidence(
    generator: CandidateGenerator,
    candidate_id: str,
) -> CandidateGenerationEvidence | None:
    method = getattr(generator, "take_evidence", None)
    if method is None:
        return None
    result = method(candidate_id)
    if result is not None and not isinstance(result, CandidateGenerationEvidence):
        raise TypeError("generator evidence provider returned an invalid value")
    return result


def _admission_detail(admission: PatchAdmission) -> str:
    if admission.rejected_paths:
        return f"{admission.code}: {', '.join(admission.rejected_paths)}"[:512]
    return admission.code
