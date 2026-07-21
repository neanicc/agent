from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class PlaywrightTestFile(ManifestModel):
    file: str = Field(min_length=1, max_length=1_024)
    projects: list[str] = Field(min_length=1, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=256)
    dependencies: list[str] = Field(default_factory=list, max_length=128)
    routes: list[str] = Field(default_factory=list, max_length=256)
    components: list[str] = Field(default_factory=list, max_length=1_024)
    imports: list[str] = Field(default_factory=list, max_length=10_000)

    @field_validator("file")
    @classmethod
    def valid_file(cls, value: str) -> str:
        return normalize_repo_path(value)

    @field_validator("dependencies", "components", "imports")
    @classmethod
    def valid_paths(cls, values: list[str]) -> list[str]:
        normalized = [normalize_repo_path(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("manifest paths must be unique")
        return sorted(normalized)

    @field_validator("projects", "tags")
    @classmethod
    def bounded_names(cls, values: list[str]) -> list[str]:
        normalized = [_name(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("manifest names must be unique")
        return sorted(normalized)

    @field_validator("routes")
    @classmethod
    def valid_routes(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for route in values:
            value = route.strip()
            if (
                not value.startswith("/")
                or len(value) > 1_024
                or "\x00" in value
                or "?" in value
                or "#" in value
            ):
                raise ValueError("manifest routes must be bounded absolute route paths")
            normalized.append(value.rstrip("/") or "/")
        if len(normalized) != len(set(normalized)):
            raise ValueError("manifest routes must be unique")
        return sorted(normalized)


class PlaywrightManifest(ManifestModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    generated_by: str = Field(min_length=1, max_length=256)
    tests: list[PlaywrightTestFile] = Field(default_factory=list, max_length=100_000)
    smoke_projects: list[str] = Field(min_length=1, max_length=128)
    shared_paths: list[str] = Field(default_factory=list, max_length=1_024)
    config_paths: list[str] = Field(default_factory=list, max_length=1_024)
    ignored_paths: list[str] = Field(default_factory=list, max_length=1_024)

    @field_validator("smoke_projects")
    @classmethod
    def valid_smoke_projects(cls, values: list[str]) -> list[str]:
        normalized = [_name(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("smoke projects must be unique")
        return sorted(normalized)

    @field_validator("shared_paths", "config_paths", "ignored_paths")
    @classmethod
    def valid_patterns(cls, values: list[str]) -> list[str]:
        normalized = [_pattern(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("manifest path patterns must be unique")
        return sorted(normalized)

    @model_validator(mode="after")
    def coherent_graph(self) -> PlaywrightManifest:
        files = [test.file for test in self.tests]
        if len(files) != len(set(files)):
            raise ValueError("manifest test files must be unique")
        known = set(files)
        for test in self.tests:
            if test.file in test.dependencies:
                raise ValueError("manifest test cannot depend on itself")
            if not set(test.dependencies).issubset(known):
                raise ValueError("manifest dependency must name a discovered test file")
        _assert_acyclic(self.tests)
        return self

    @classmethod
    def from_path(cls, path: str | Path) -> PlaywrightManifest:
        source = Path(path)
        if source.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Playwright manifest exceeds 16 MiB")
        try:
            return cls.model_validate_json(source.read_bytes())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Playwright manifest is invalid") from exc


def normalize_repo_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or path.as_posix() != normalized
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\x00" in normalized
        or len(normalized) > 1_024
    ):
        raise ValueError("manifest path must be a safe repository-relative path")
    return path.as_posix()


def _pattern(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\x00" in normalized
        or ".." in PurePosixPath(normalized).parts
        or len(normalized) > 1_024
    ):
        raise ValueError("manifest pattern must be safe and repository-relative")
    return normalized


def _name(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 256 or "\x00" in normalized:
        raise ValueError("manifest name must be bounded and non-empty")
    return normalized


def _assert_acyclic(tests: list[PlaywrightTestFile]) -> None:
    graph = {test.file: test.dependencies for test in tests}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(path: str) -> None:
        if path in visiting:
            raise ValueError("manifest test dependency graph contains a cycle")
        if path in visited:
            return
        visiting.add(path)
        for dependency in graph[path]:
            visit(dependency)
        visiting.remove(path)
        visited.add(path)

    for path in sorted(graph):
        visit(path)
