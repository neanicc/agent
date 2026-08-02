from __future__ import annotations

import ast
import os
import posixpath
import re
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from loopguard.browser.manifest import PlaywrightManifest


_EXCLUDED_PARTS = {
    ".git",
    ".loopguard",
    ".next",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
}
_TS_IMPORT = re.compile(
    r"(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)['\"](?P<path>\.[^'\"]+)['\"]"
)


def _repository_path(value: str) -> str:
    if not value or len(value) > 4096 or "\\" in value or "\x00" in value:
        raise ValueError("path must use a non-empty repository-relative POSIX form")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must remain repository-relative")
    return path.as_posix()


class ImpactEdgeKind(StrEnum):
    CALLS = "calls"
    IMPORTS = "imports"
    COVERS = "covers"
    CONVENTION = "convention"


class ChangeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    symbols: list[str] = Field(default_factory=list, max_length=4096)
    deleted: bool = False
    before_hash: str | None = None
    after_hash: str | None = None

    _path = field_validator("path")(_repository_path)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 1024 for value in normalized):
            raise ValueError("symbols must not be empty")
        return list(dict.fromkeys(normalized))


class ImpactEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: str
    target_path: str
    kind: ImpactEdgeKind
    source_symbol: str = "*"
    target_symbol: str = "*"

    _source_path = field_validator("source_path")(_repository_path)
    _target_path = field_validator("target_path")(_repository_path)

    @field_validator("source_symbol", "target_symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("edge symbols must not be empty")
        return normalized


class ImpactContribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin: str
    edges: list[ImpactEdge] = Field(default_factory=list, max_length=100_000)
    candidates: dict[str, list[str]] = Field(default_factory=dict, max_length=100_000)

    @field_validator("candidates")
    @classmethod
    def validate_candidates(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        normalized: dict[str, list[str]] = {}
        for path, reasons in value.items():
            clean_reasons = list(dict.fromkeys(reason.strip() for reason in reasons))
            if not clean_reasons or any(not reason or len(reason) > 4096 for reason in clean_reasons):
                raise ValueError("impact candidates require bounded explanations")
            normalized[_repository_path(path)] = clean_reasons
        return normalized


class ImpactPlugin(Protocol):
    name: str

    def analyze(self, repo: Path, changes: list[ChangeRecord]) -> ImpactContribution: ...


class PythonImpactPlugin:
    name = "python"

    def __init__(self, *, max_files: int = 20_000, max_file_bytes: int = 2_000_000) -> None:
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes

    def analyze(self, repo: Path, changes: list[ChangeRecord]) -> ImpactContribution:
        root = _repository_root(repo)
        files = _bounded_files(root, {".py"}, self.max_files)
        modules: dict[str, str] = {}
        for path in files:
            relative = path.relative_to(root).as_posix()
            for module in _python_module_names(relative):
                modules[module] = relative
        edges: list[ImpactEdge] = []
        for path in files:
            if path.stat().st_size > self.max_file_bytes:
                continue
            relative = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            for module, symbol in _python_imports(tree, relative):
                target = _resolve_python_module(module, modules)
                if target is None or target == relative:
                    continue
                edges.append(
                    ImpactEdge(
                        source_path=relative,
                        source_symbol="*",
                        target_path=target,
                        target_symbol=symbol,
                        kind=ImpactEdgeKind.IMPORTS,
                    )
                )
        candidates: dict[str, list[str]] = {}
        existing = {path.relative_to(root).as_posix() for path in files}
        for change in changes:
            if PurePosixPath(change.path).suffix != ".py":
                continue
            for candidate in _python_test_candidates(change.path):
                if candidate in existing:
                    candidates.setdefault(candidate, []).append(
                        f"convention:{change.path}"
                    )
        return ImpactContribution(
            plugin=self.name,
            edges=_unique_edges(edges),
            candidates=candidates,
        )


class TypeScriptImpactPlugin:
    name = "typescript"
    extensions = {".ts", ".tsx", ".js", ".jsx"}

    def __init__(self, *, max_files: int = 20_000, max_file_bytes: int = 2_000_000) -> None:
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes

    def analyze(self, repo: Path, changes: list[ChangeRecord]) -> ImpactContribution:
        root = _repository_root(repo)
        files = _bounded_files(root, self.extensions, self.max_files)
        existing = {path.relative_to(root).as_posix(): path for path in files}
        edges: list[ImpactEdge] = []
        for relative, path in existing.items():
            if path.stat().st_size > self.max_file_bytes:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for match in _TS_IMPORT.finditer(source):
                target = _resolve_typescript_import(relative, match.group("path"), existing)
                if target is None or target == relative:
                    continue
                edges.append(
                    ImpactEdge(
                        source_path=relative,
                        target_path=target,
                        kind=ImpactEdgeKind.IMPORTS,
                    )
                )
        candidates: dict[str, list[str]] = {}
        for change in changes:
            if PurePosixPath(change.path).suffix not in self.extensions:
                continue
            for candidate in _typescript_test_candidates(change.path):
                if candidate in existing:
                    candidates.setdefault(candidate, []).append(
                        f"convention:{change.path}"
                    )
        return ImpactContribution(
            plugin=self.name,
            edges=_unique_edges(edges),
            candidates=candidates,
        )


class PlaywrightImpactPlugin:
    """Contributes offline Playwright coverage to the shared impact graph."""

    name = "playwright"

    def __init__(
        self,
        manifest: PlaywrightManifest,
        *,
        dependency_edges: dict[str, list[str]] | None = None,
    ) -> None:
        from loopguard.browser.manifest import PlaywrightManifest
        from loopguard.browser.selection import PlaywrightSelector

        self.manifest = PlaywrightManifest.model_validate(manifest)
        self.selector = PlaywrightSelector(
            self.manifest, dependency_edges=dependency_edges
        )

    def analyze(self, repo: Path, changes: list[ChangeRecord]) -> ImpactContribution:
        _repository_root(repo)
        selection = self.selector.select([change.path for change in changes])
        candidates = {
            path: reasons
            for path, reasons in selection.explanations.items()
            if not path.startswith("project:")
        }
        if selection.confidence == "fallback":
            fallback_reasons = sorted(
                {
                    reason
                    for key, reasons in selection.explanations.items()
                    if key.startswith("project:")
                    for reason in reasons
                }
            )
            for test in self.manifest.tests:
                candidates.setdefault(
                    test.file,
                    [f"smoke-fallback:{reason}" for reason in fallback_reasons]
                    or ["smoke-fallback:unknown-impact"],
                )

        edges: list[ImpactEdge] = []
        for test in self.manifest.tests:
            for target in [*test.imports, *test.components, *test.dependencies]:
                edges.append(
                    ImpactEdge(
                        source_path=test.file,
                        target_path=target,
                        kind=ImpactEdgeKind.COVERS,
                    )
                )
        return ImpactContribution(
            plugin=self.name,
            edges=_unique_edges(edges),
            candidates=candidates,
        )


def _repository_root(repo: Path) -> Path:
    root = repo.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("repository must be a directory")
    return root


def _bounded_files(root: Path, extensions: set[str], maximum: int) -> list[Path]:
    files: list[Path] = []
    for directory, child_directories, names in os.walk(root, followlinks=False):
        child_directories[:] = sorted(
            name
            for name in child_directories
            if name not in _EXCLUDED_PARTS
            and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_symlink() or path.suffix not in extensions:
                continue
            files.append(path)
            if len(files) > maximum:
                raise ValueError("impact plugin file limit exceeded")
    return files


def _python_module_names(relative: str) -> set[str]:
    path = PurePosixPath(relative)
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    names = {".".join(parts)}
    if parts and parts[0] in {"src", "lib"}:
        names.add(".".join(parts[1:]))
    return {name for name in names if name}


def _python_imports(tree: ast.AST, importer: str) -> list[tuple[str, str]]:
    imports: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, "*") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_python_import(importer, node.module, node.level)
            if module:
                imports.extend((module, alias.name) for alias in node.names)
            elif node.level:
                base = _absolute_python_import(importer, None, node.level)
                imports.extend((f"{base}.{alias.name}", "*") for alias in node.names)
    return imports


def _absolute_python_import(importer: str, module: str | None, level: int) -> str:
    if level == 0:
        return module or ""
    importer_names = sorted(_python_module_names(importer), key=lambda value: value.count("."))
    if not importer_names:
        return ""
    package = importer_names[0].split(".")[:-1]
    remove = level - 1
    if remove > len(package):
        return ""
    base = package[: len(package) - remove if remove else None]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _resolve_python_module(module: str, modules: dict[str, str]) -> str | None:
    current = module
    while current:
        if current in modules:
            return modules[current]
        current = current.rpartition(".")[0]
    return None


def _python_test_candidates(relative: str) -> set[str]:
    path = PurePosixPath(relative)
    stem = path.stem
    return {
        (path.parent / f"test_{stem}.py").as_posix(),
        (path.parent / f"{stem}_test.py").as_posix(),
        (PurePosixPath("tests") / f"test_{stem}.py").as_posix(),
        (PurePosixPath("test") / f"test_{stem}.py").as_posix(),
    }


def _resolve_typescript_import(
    importer: str,
    requested: str,
    existing: dict[str, Path],
) -> str | None:
    normalized = posixpath.normpath(
        posixpath.join(PurePosixPath(importer).parent.as_posix(), requested)
    )
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        return None
    base = PurePosixPath(normalized)
    candidates = [base.as_posix()]
    if base.suffix not in TypeScriptImpactPlugin.extensions:
        candidates.extend(f"{base.as_posix()}{suffix}" for suffix in sorted(TypeScriptImpactPlugin.extensions))
        candidates.extend(
            (base / f"index{suffix}").as_posix()
            for suffix in sorted(TypeScriptImpactPlugin.extensions)
        )
    for candidate in candidates:
        normalized_candidate = PurePosixPath(candidate)
        if normalized_candidate.as_posix() in existing:
            return normalized_candidate.as_posix()
    return None


def _typescript_test_candidates(relative: str) -> set[str]:
    path = PurePosixPath(relative)
    stem = path.stem
    candidates: set[str] = set()
    for extension in TypeScriptImpactPlugin.extensions:
        candidates.add((path.parent / f"{stem}.test{extension}").as_posix())
        candidates.add((path.parent / f"{stem}.spec{extension}").as_posix())
    return candidates


def _unique_edges(edges: list[ImpactEdge]) -> list[ImpactEdge]:
    unique: dict[tuple[str, str, str, str, str], ImpactEdge] = {}
    for edge in edges:
        key = (
            edge.source_path,
            edge.source_symbol,
            edge.target_path,
            edge.target_symbol,
            edge.kind,
        )
        unique[key] = edge
    return list(unique.values())
