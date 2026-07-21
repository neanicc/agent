from __future__ import annotations

from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict, Field

from .manifest import PlaywrightManifest, normalize_repo_path


class PlaywrightSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    test_files: list[str] = Field(default_factory=list, max_length=100_000)
    projects: list[str] = Field(default_factory=list, max_length=1_024)
    confidence: str
    explanations: dict[str, list[str]] = Field(default_factory=dict)


class PlaywrightSelector:
    def __init__(
        self,
        manifest: PlaywrightManifest,
        *,
        dependency_edges: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.manifest = PlaywrightManifest.model_validate(manifest)
        self._tests = {test.file: test for test in self.manifest.tests}
        self._dependency_edges: dict[str, tuple[str, ...]] = {}
        for source, consumers in (dependency_edges or {}).items():
            path = normalize_repo_path(source)
            normalized = tuple(sorted({normalize_repo_path(item) for item in consumers}))
            if not set(normalized).issubset(self._tests):
                raise ValueError("dependency graph consumer is not a discovered Playwright test")
            self._dependency_edges[path] = normalized

    def select(self, changed_paths: Sequence[str]) -> PlaywrightSelection:
        if len(changed_paths) > 100_000:
            raise ValueError("changed path input exceeds the selection limit")
        paths = sorted({normalize_repo_path(path) for path in changed_paths})
        selected: set[str] = set()
        explanations: dict[str, set[str]] = {}
        fallback_reasons: list[str] = []

        for path in paths:
            if _matches(path, self.manifest.ignored_paths):
                continue
            if _matches(path, self.manifest.config_paths):
                fallback_reasons.append(f"configuration-change:{path}")
                continue
            if _matches(path, self.manifest.shared_paths):
                fallback_reasons.append(f"shared-change:{path}")
                continue

            matched = False
            route = _route_for_path(path)
            if route is not None:
                for test in self.manifest.tests:
                    if route in test.routes:
                        _select(selected, explanations, test.file, f"covers-route:{route}")
                        matched = True
            if matched:
                continue

            for test in self.manifest.tests:
                if path in test.components:
                    _select(
                        selected,
                        explanations,
                        test.file,
                        f"covers-component:{path}",
                    )
                    matched = True
            if path in self._tests:
                _select(selected, explanations, path, f"changed-test:{path}")
                matched = True
            for consumer in self._dependency_edges.get(path, ()):
                _select(selected, explanations, consumer, f"dependency-graph:{path}")
                matched = True
            for test in self.manifest.tests:
                if path in test.imports:
                    _select(selected, explanations, test.file, f"imports:{path}")
                    matched = True
            if not matched:
                fallback_reasons.append(f"unknown-impact:{path}")

        roots = sorted(selected)
        for root in roots:
            self._add_dependencies(root, selected, explanations, trail=set())
        ordered = self._topological(selected)
        projects = {
            project
            for path in ordered
            for project in self._tests[path].projects
        }
        if fallback_reasons:
            projects.update(self.manifest.smoke_projects)
            for project in self.manifest.smoke_projects:
                explanations.setdefault(f"project:{project}", set()).update(fallback_reasons)

        return PlaywrightSelection(
            test_files=ordered,
            projects=sorted(projects),
            confidence="fallback" if fallback_reasons else "exact",
            explanations={key: sorted(values) for key, values in sorted(explanations.items())},
        )

    def _add_dependencies(
        self,
        path: str,
        selected: set[str],
        explanations: dict[str, set[str]],
        *,
        trail: set[str],
    ) -> None:
        if path in trail:
            raise ValueError("Playwright dependency cycle reached during selection")
        trail = {*trail, path}
        for dependency in self._tests[path].dependencies:
            selected.add(dependency)
            explanations.setdefault(dependency, set()).add(f"dependency-of:{path}")
            self._add_dependencies(dependency, selected, explanations, trail=trail)

    def _topological(self, selected: set[str]) -> list[str]:
        ordered: list[str] = []
        visited: set[str] = set()

        def visit(path: str) -> None:
            if path in visited:
                return
            for dependency in self._tests[path].dependencies:
                if dependency in selected:
                    visit(dependency)
            visited.add(path)
            ordered.append(path)

        for path in sorted(selected):
            visit(path)
        return ordered


def _select(
    selected: set[str],
    explanations: dict[str, set[str]],
    path: str,
    reason: str,
) -> None:
    selected.add(path)
    explanations.setdefault(path, set()).add(reason)


def _matches(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def _route_for_path(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "app" and parts[-1].startswith("page."):
        route_parts = [
            part
            for part in parts[1:-1]
            if not (part.startswith("(") and part.endswith(")")) and not part.startswith("@")
        ]
        return "/" + "/".join(route_parts) if route_parts else "/"
    if len(parts) >= 2 and parts[0] == "pages":
        stem = parts[-1].split(".", 1)[0]
        route_parts = [*parts[1:-1], stem]
        if route_parts[-1] == "index":
            route_parts.pop()
        return "/" + "/".join(route_parts) if route_parts else "/"
    return None
