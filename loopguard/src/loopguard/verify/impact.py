from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import CheckPhase, CheckSpec
from .plugins import (
    ChangeRecord,
    ImpactContribution,
    ImpactEdge,
    ImpactEdgeKind,
    ImpactPlugin,
    PythonImpactPlugin,
    TypeScriptImpactPlugin,
)


class ImpactStatus(StrEnum):
    SELECTIVE = "selective"
    FULL_SUITE = "full_suite"
    NO_VERIFIED_SUITE = "no_verified_suite"


class ImpactGraphSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    worktree_hash: str
    indexed_paths: list[str] = Field(max_length=100_000)
    edges: list[ImpactEdge] = Field(default_factory=list, max_length=500_000)
    stale: bool = False

    @field_validator("worktree_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("worktree hash must be a SHA-256 digest")
        return normalized

    @field_validator("indexed_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        normalized = [ChangeRecord(path=value).path for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("indexed paths must be unique")
        return normalized

    @model_validator(mode="after")
    def edges_must_reference_indexed_paths(self) -> ImpactGraphSnapshot:
        paths = set(self.indexed_paths)
        if any(
            edge.source_path not in paths or edge.target_path not in paths
            for edge in self.edges
        ):
            raise ValueError("index edges must reference indexed paths")
        return self


class ImpactSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ImpactStatus
    test_paths: list[str] = Field(default_factory=list, max_length=100_000)
    dependent_paths: list[str] = Field(default_factory=list, max_length=100_000)
    explanation: dict[str, list[str]] = Field(default_factory=dict)
    checks: list[CheckSpec] = Field(default_factory=list, max_length=1024)
    reason: str | None = None
    nodes_visited: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def selective_results_require_explained_tests_and_checks(self) -> ImpactSelection:
        if self.status is ImpactStatus.SELECTIVE:
            if not self.test_paths or not self.checks:
                raise ValueError("selective impact requires tests and checks")
            if any(not self.explanation.get(path) for path in self.test_paths):
                raise ValueError("every selected test requires an explanation")
        if self.status is ImpactStatus.NO_VERIFIED_SUITE and self.checks:
            raise ValueError("no-verified-suite impact cannot contain checks")
        return self


class ImpactAnalyzer:
    def __init__(
        self,
        index: ImpactGraphSnapshot | None,
        *,
        expected_worktree_hash: str,
        trusted_full_suite: CheckSpec | None,
        plugins: Sequence[ImpactPlugin] | None = None,
        historical_failures: Mapping[str, Sequence[str]] | None = None,
        coverage_edges: Sequence[ImpactEdge] | None = None,
        max_depth: int = 4,
        max_nodes: int = 10_000,
    ) -> None:
        if max_depth < 1 or max_nodes < 1:
            raise ValueError("impact traversal limits must be positive")
        self.index = index
        self.expected_worktree_hash = ImpactGraphSnapshot(
            worktree_hash=expected_worktree_hash,
            indexed_paths=[],
        ).worktree_hash
        self.trusted_full_suite = trusted_full_suite
        self.plugins = list(
            plugins if plugins is not None else [PythonImpactPlugin(), TypeScriptImpactPlugin()]
        )
        self.historical_failures = {
            ChangeRecord(path=path).path: [ChangeRecord(path=item).path for item in tests]
            for path, tests in (historical_failures or {}).items()
        }
        self.coverage_edges = list(coverage_edges or [])
        if any(edge.kind is not ImpactEdgeKind.COVERS for edge in self.coverage_edges):
            raise ValueError("optional coverage edges must use the covers edge kind")
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def analyze(self, repository: Path, changes: list[ChangeRecord]) -> ImpactSelection:
        try:
            repo = repository.expanduser().resolve(strict=True)
        except OSError:
            return self._fallback("repository_unavailable")
        if not repo.is_dir():
            return self._fallback("repository_unavailable")
        if not changes:
            return self._fallback("changes_missing")
        if self.index is None:
            return self._fallback("context_index_missing")
        if self.index.stale or self.index.worktree_hash != self.expected_worktree_hash:
            return self._fallback("context_index_stale")
        indexed_paths = set(self.index.indexed_paths)
        if any(change.path not in indexed_paths for change in changes):
            return self._fallback("coverage_unproven")
        try:
            contributions = [plugin.analyze(repo, changes) for plugin in self.plugins]
        except (OSError, ValueError):
            return self._fallback("impact_plugin_failed")
        edges = [*self.index.edges, *self.coverage_edges]
        for contribution in contributions:
            edges.extend(contribution.edges)
        traversal = self._traverse(changes, edges, repo)
        if traversal.truncated:
            return self._fallback(
                "impact_traversal_incomplete",
                nodes_visited=traversal.nodes_visited,
            )
        if not all(traversal.covered.values()):
            return self._fallback("coverage_unproven", nodes_visited=traversal.nodes_visited)
        selected = set(traversal.test_paths)
        explanation = {path: list(reasons) for path, reasons in traversal.explanation.items()}
        for contribution in contributions:
            _add_candidates(selected, explanation, contribution, repo)
        for change in changes:
            for test_path in self.historical_failures.get(change.path, []):
                if (
                    test_path in selected
                    or not _is_test_path(test_path)
                    or not _safe_existing_file(repo, test_path)
                ):
                    continue
                selected.add(test_path)
                explanation[test_path] = [f"historical_failure:{change.path}"]
        checks = _checks_for_tests(sorted(selected))
        if checks is None:
            return self._fallback("unsupported_impacted_test_type")
        return ImpactSelection(
            status=ImpactStatus.SELECTIVE,
            test_paths=sorted(selected),
            dependent_paths=sorted(traversal.dependent_paths - selected),
            explanation={path: sorted(set(explanation[path])) for path in sorted(selected)},
            checks=checks,
            nodes_visited=traversal.nodes_visited,
        )

    def _traverse(
        self,
        changes: list[ChangeRecord],
        edges: list[ImpactEdge],
        repository: Path,
    ) -> _Traversal:
        incoming: dict[str, list[ImpactEdge]] = defaultdict(list)
        for edge in edges:
            incoming[edge.target_path].append(edge)
        seeds = [
            (change.path, symbol)
            for change in changes
            for symbol in (change.symbols or ["*"])
        ]
        covered = {seed: False for seed in seeds}
        queue: deque[tuple[tuple[str, str], tuple[str, str], int]] = deque()
        scheduled: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        truncated = False
        for seed in seeds:
            key = (seed, seed)
            if len(scheduled) >= self.max_nodes:
                truncated = True
                break
            queue.append((seed, seed, 0))
            scheduled.add(key)
        visited: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        tests: set[str] = set()
        dependents: set[str] = set()
        explanation: dict[str, list[str]] = defaultdict(list)
        while queue and not truncated:
            origin, node, depth = queue.popleft()
            key = (origin, node)
            scheduled.discard(key)
            if key in visited:
                continue
            visited.add(key)
            relevant = [
                edge
                for edge in incoming.get(node[0], [])
                if _symbol_matches(node[1], edge.target_symbol)
            ]
            if depth >= self.max_depth:
                if any((origin, (edge.source_path, edge.source_symbol)) not in visited for edge in relevant):
                    truncated = True
                continue
            for edge in relevant:
                source = (edge.source_path, edge.source_symbol)
                if edge.kind is ImpactEdgeKind.COVERS:
                    if _is_test_path(edge.source_path) and _safe_existing_file(
                        repository, edge.source_path
                    ):
                        covered[origin] = True
                        tests.add(edge.source_path)
                        explanation[edge.source_path].append(
                            f"covers:{origin[0]}:{origin[1]}"
                        )
                    continue
                dependents.add(edge.source_path)
                source_key = (origin, source)
                if source_key in visited or source_key in scheduled:
                    continue
                if len(visited) + len(scheduled) >= self.max_nodes:
                    truncated = True
                    break
                queue.append((origin, source, depth + 1))
                scheduled.add(source_key)
        return _Traversal(
            covered=covered,
            test_paths=tests,
            dependent_paths=dependents,
            explanation=explanation,
            nodes_visited=len(visited),
            truncated=truncated,
        )

    def _fallback(self, reason: str, *, nodes_visited: int = 0) -> ImpactSelection:
        if self.trusted_full_suite is None:
            return ImpactSelection(
                status=ImpactStatus.NO_VERIFIED_SUITE,
                reason="no_verified_suite",
                explanation={"no_verified_suite": [reason]},
                nodes_visited=nodes_visited,
            )
        return ImpactSelection(
            status=ImpactStatus.FULL_SUITE,
            checks=[self.trusted_full_suite],
            reason=reason,
            explanation={self.trusted_full_suite.id: [reason]},
            nodes_visited=nodes_visited,
        )


class _Traversal(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    covered: dict[tuple[str, str], bool]
    test_paths: set[str]
    dependent_paths: set[str]
    explanation: dict[str, list[str]]
    nodes_visited: int
    truncated: bool


def _symbol_matches(active: str, target: str) -> bool:
    return active == "*" or target == "*" or active == target


def _safe_existing_file(repository: Path, relative: str) -> bool:
    try:
        raw = ChangeRecord(path=relative).path
        candidate = repository.joinpath(*PurePosixPath(raw).parts)
        current = repository
        for part in PurePosixPath(raw).parts:
            current /= part
            if current.is_symlink():
                return False
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repository)
    except (OSError, ValueError):
        return False
    return resolved.is_file()


def _is_test_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    name = path.name.lower()
    directory_markers = {"test", "tests", "spec", "specs", "__tests__"}
    if path.suffix == ".py":
        return (
            any(part.lower() in directory_markers for part in path.parts[:-1])
            or name.startswith("test_")
            or name.endswith("_test.py")
            or name.endswith("_spec.py")
        )
    return any(marker in name for marker in (".test.", ".spec.")) or any(
        part.lower() in directory_markers for part in path.parts[:-1]
    )


def _add_candidates(
    selected: set[str],
    explanation: dict[str, list[str]],
    contribution: ImpactContribution,
    repository: Path,
) -> None:
    for path, reasons in contribution.candidates.items():
        if (
            path in selected
            or not _is_test_path(path)
            or not _safe_existing_file(repository, path)
        ):
            continue
        selected.add(path)
        explanation[path] = list(reasons)


def _checks_for_tests(test_paths: list[str]) -> list[CheckSpec] | None:
    python = [path for path in test_paths if PurePosixPath(path).suffix == ".py"]
    typescript = [
        path
        for path in test_paths
        if PurePosixPath(path).suffix in TypeScriptImpactPlugin.extensions
    ]
    if len(python) + len(typescript) != len(test_paths):
        return None
    checks: list[CheckSpec] = []
    checks.extend(_chunked_checks("python", python, ["python", "-m", "pytest", "-q"]))
    checks.extend(
        _chunked_checks(
            "typescript",
            typescript,
            ["npm", "test", "--", "--runInBand"],
        )
    )
    return checks


def _chunked_checks(prefix: str, paths: list[str], command: list[str]) -> list[CheckSpec]:
    checks: list[CheckSpec] = []
    chunk_size = 200
    for offset in range(0, len(paths), chunk_size):
        chunk = paths[offset : offset + chunk_size]
        digest = hashlib.sha256("\0".join(chunk).encode()).hexdigest()[:12]
        checks.append(
            CheckSpec(
                id=f"impacted-{prefix}-{digest}",
                command=[*command, *chunk],
                phase=CheckPhase.IMPACTED,
            )
        )
    return checks


__all__ = [
    "ChangeRecord",
    "ImpactAnalyzer",
    "ImpactEdge",
    "ImpactGraphSnapshot",
    "ImpactSelection",
    "ImpactStatus",
]
