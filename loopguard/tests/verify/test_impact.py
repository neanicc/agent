from __future__ import annotations

from pathlib import Path

from loopguard.verify.impact import (
    ChangeRecord,
    ImpactAnalyzer,
    ImpactEdge,
    ImpactGraphSnapshot,
    ImpactStatus,
)
from loopguard.verify.models import CheckPhase, CheckSpec
from loopguard.verify.plugins import PythonImpactPlugin, TypeScriptImpactPlugin


WORKTREE_HASH = "a" * 64
FULL_SUITE = CheckSpec(id="full", command=["python", "-m", "pytest", "-q"])


def _index(*edges: ImpactEdge, paths: list[str] | None = None) -> ImpactGraphSnapshot:
    return ImpactGraphSnapshot(
        worktree_hash=WORKTREE_HASH,
        indexed_paths=paths or ["src/auth.py", "src/routes/login.py", "tests/test_auth.py"],
        edges=list(edges),
    )


def test_changed_symbol_selects_direct_tests_and_callers(tmp_path: Path) -> None:
    for relative in ("src/auth.py", "src/routes/login.py", "tests/test_auth.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    analyzer = ImpactAnalyzer(
        _index(
            ImpactEdge(
                source_path="src/routes/login.py",
                source_symbol="route",
                target_path="src/auth.py",
                target_symbol="login",
                kind="calls",
            ),
            ImpactEdge(
                source_path="tests/test_auth.py",
                source_symbol="test_login",
                target_path="src/auth.py",
                target_symbol="login",
                kind="covers",
            ),
        ),
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
    )

    impact = analyzer.analyze(
        tmp_path,
        [ChangeRecord(path="src/auth.py", symbols=["login"])],
    )

    assert impact.status is ImpactStatus.SELECTIVE
    assert impact.test_paths == ["tests/test_auth.py"]
    assert impact.dependent_paths == ["src/routes/login.py"]
    assert impact.explanation["tests/test_auth.py"] == ["covers:src/auth.py:login"]
    assert impact.checks[0].phase is CheckPhase.IMPACTED
    assert impact.checks[0].command[-1] == "tests/test_auth.py"


def test_deleted_file_uses_retained_index_edges(tmp_path: Path) -> None:
    test_path = tmp_path / "tests/test_removed.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("def test_removed(): pass\n")
    analyzer = ImpactAnalyzer(
        _index(
            ImpactEdge(
                source_path="tests/test_removed.py",
                source_symbol="test_removed",
                target_path="src/removed.py",
                target_symbol="removed",
                kind="covers",
            ),
            paths=["src/removed.py", "tests/test_removed.py"],
        ),
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
    )

    impact = analyzer.analyze(
        tmp_path,
        [ChangeRecord(path="src/removed.py", symbols=["removed"], deleted=True)],
    )

    assert impact.status is ImpactStatus.SELECTIVE
    assert impact.test_paths == ["tests/test_removed.py"]


def test_cycles_are_bounded_and_still_find_coverage(tmp_path: Path) -> None:
    for relative in ("src/a.py", "src/b.py", "tests/test_a.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    analyzer = ImpactAnalyzer(
        _index(
            ImpactEdge(
                source_path="src/b.py",
                target_path="src/a.py",
                kind="imports",
            ),
            ImpactEdge(
                source_path="src/a.py",
                target_path="src/b.py",
                kind="imports",
            ),
            ImpactEdge(
                source_path="tests/test_a.py",
                target_path="src/b.py",
                kind="covers",
            ),
            paths=["src/a.py", "src/b.py", "tests/test_a.py"],
        ),
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
        max_depth=3,
        max_nodes=8,
    )

    impact = analyzer.analyze(tmp_path, [ChangeRecord(path="src/a.py")])

    assert impact.status is ImpactStatus.SELECTIVE
    assert impact.test_paths == ["tests/test_a.py"]
    assert impact.nodes_visited <= 8


def test_depth_or_node_truncation_falls_back_to_trusted_full_suite(tmp_path: Path) -> None:
    for relative in ("src/a.py", "src/b.py", "tests/test_b.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    index = _index(
        ImpactEdge(source_path="src/b.py", target_path="src/a.py", kind="imports"),
        ImpactEdge(source_path="tests/test_b.py", target_path="src/b.py", kind="covers"),
        paths=["src/a.py", "src/b.py", "tests/test_b.py"],
    )
    analyzer = ImpactAnalyzer(
        index,
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
        max_depth=1,
    )

    impact = analyzer.analyze(tmp_path, [ChangeRecord(path="src/a.py")])

    assert impact.status is ImpactStatus.FULL_SUITE
    assert impact.reason == "impact_traversal_incomplete"
    assert impact.checks == [FULL_SUITE]


def test_node_limit_truncation_falls_back_instead_of_returning_partial_coverage(
    tmp_path: Path,
) -> None:
    for relative in ("src/a.py", "src/b.py", "src/c.py", "tests/test_c.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    analyzer = ImpactAnalyzer(
        _index(
            ImpactEdge(source_path="src/b.py", target_path="src/a.py", kind="imports"),
            ImpactEdge(source_path="src/c.py", target_path="src/a.py", kind="imports"),
            ImpactEdge(source_path="tests/test_c.py", target_path="src/c.py", kind="covers"),
            paths=["src/a.py", "src/b.py", "src/c.py", "tests/test_c.py"],
        ),
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
        max_nodes=1,
    )

    impact = analyzer.analyze(tmp_path, [ChangeRecord(path="src/a.py")])

    assert impact.status is ImpactStatus.FULL_SUITE
    assert impact.reason == "impact_traversal_incomplete"


def test_missing_stale_or_unproven_index_never_guesses_a_slice(tmp_path: Path) -> None:
    source = tmp_path / "src/auth.py"
    source.parent.mkdir()
    source.write_text("def login(): pass\n")
    test = tmp_path / "tests/test_auth.py"
    test.parent.mkdir()
    test.write_text("def test_login(): pass\n")
    change = [ChangeRecord(path="src/auth.py", symbols=["login"])]

    missing = ImpactAnalyzer(
        None,
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
    ).analyze(tmp_path, change)
    stale = ImpactAnalyzer(
        _index(),
        expected_worktree_hash="b" * 64,
        trusted_full_suite=FULL_SUITE,
    ).analyze(tmp_path, change)
    unproven = ImpactAnalyzer(
        _index(paths=["src/auth.py", "tests/test_auth.py"]),
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
        plugins=[PythonImpactPlugin()],
    ).analyze(tmp_path, change)

    assert missing.status is stale.status is unproven.status is ImpactStatus.FULL_SUITE
    assert missing.reason == "context_index_missing"
    assert stale.reason == "context_index_stale"
    assert unproven.reason == "coverage_unproven"


def test_no_trusted_suite_returns_typed_inconclusive_result(tmp_path: Path) -> None:
    impact = ImpactAnalyzer(
        None,
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=None,
    ).analyze(tmp_path, [ChangeRecord(path="src/auth.py")])

    assert impact.status is ImpactStatus.NO_VERIFIED_SUITE
    assert impact.reason == "no_verified_suite"
    assert impact.checks == []


def test_python_plugin_emits_import_and_test_convention_edges(tmp_path: Path) -> None:
    source = tmp_path / "src/acme/service.py"
    caller = tmp_path / "src/acme/routes.py"
    test = tmp_path / "tests/test_service.py"
    for path in (source, caller, test):
        path.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def load_user(): return 1\n")
    caller.write_text("from acme.service import load_user\n")
    test.write_text("def test_load_user(): pass\n")

    contribution = PythonImpactPlugin().analyze(
        tmp_path,
        [ChangeRecord(path="src/acme/service.py", symbols=["load_user"])],
    )

    assert any(
        edge.source_path == "src/acme/routes.py"
        and edge.target_path == "src/acme/service.py"
        and edge.kind == "imports"
        for edge in contribution.edges
    )
    assert contribution.candidates["tests/test_service.py"] == [
        "convention:src/acme/service.py"
    ]


def test_python_plugin_resolves_relative_imports(tmp_path: Path) -> None:
    package = tmp_path / "src/acme"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "service.py").write_text("def load_user(): return 1\n")
    (package / "routes.py").write_text("from .service import load_user\n")

    contribution = PythonImpactPlugin().analyze(
        tmp_path,
        [ChangeRecord(path="src/acme/service.py", symbols=["load_user"])],
    )

    assert any(
        edge.source_path == "src/acme/routes.py"
        and edge.target_path == "src/acme/service.py"
        and edge.target_symbol == "load_user"
        for edge in contribution.edges
    )


def test_typescript_plugin_emits_import_and_test_name_conventions(tmp_path: Path) -> None:
    source = tmp_path / "src/auth.ts"
    caller = tmp_path / "src/login.tsx"
    test = tmp_path / "src/auth.test.ts"
    source.parent.mkdir()
    source.write_text("export function login() { return true }\n")
    caller.write_text("import { login } from './auth';\n")
    test.write_text("test('login', () => {})\n")

    contribution = TypeScriptImpactPlugin().analyze(
        tmp_path,
        [ChangeRecord(path="src/auth.ts", symbols=["login"])],
    )

    assert any(
        edge.source_path == "src/login.tsx"
        and edge.target_path == "src/auth.ts"
        and edge.kind == "imports"
        for edge in contribution.edges
    )
    assert contribution.candidates["src/auth.test.ts"] == ["convention:src/auth.ts"]


def test_typescript_plugin_resolves_parent_relative_imports(tmp_path: Path) -> None:
    source = tmp_path / "src/auth.ts"
    caller = tmp_path / "src/routes/login.ts"
    caller.parent.mkdir(parents=True)
    source.write_text("export function login() { return true }\n")
    caller.write_text("import { login } from '../auth';\n")

    contribution = TypeScriptImpactPlugin().analyze(
        tmp_path,
        [ChangeRecord(path="src/auth.ts", symbols=["login"])],
    )

    assert any(
        edge.source_path == "src/routes/login.ts"
        and edge.target_path == "src/auth.ts"
        for edge in contribution.edges
    )


def test_symlinked_test_evidence_cannot_prove_selective_coverage(tmp_path: Path) -> None:
    source = tmp_path / "src/auth.py"
    source.parent.mkdir()
    source.write_text("def login(): pass\n")
    outside = tmp_path.parent / "outside-impact-test.py"
    outside.write_text("def test_login(): pass\n")
    test = tmp_path / "tests/test_auth.py"
    test.parent.mkdir()
    test.symlink_to(outside)
    analyzer = ImpactAnalyzer(
        _index(
            ImpactEdge(
                source_path="tests/test_auth.py",
                target_path="src/auth.py",
                kind="covers",
            ),
            paths=["src/auth.py", "tests/test_auth.py"],
        ),
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
    )

    impact = analyzer.analyze(tmp_path, [ChangeRecord(path="src/auth.py")])

    assert impact.status is ImpactStatus.FULL_SUITE
    assert impact.reason == "coverage_unproven"


def test_coverage_edge_to_non_test_source_cannot_be_executed_as_a_test(
    tmp_path: Path,
) -> None:
    for relative in ("src/auth.py", "src/production_helper.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    analyzer = ImpactAnalyzer(
        _index(
            ImpactEdge(
                source_path="src/production_helper.py",
                target_path="src/auth.py",
                kind="covers",
            ),
            paths=["src/auth.py", "src/production_helper.py"],
        ),
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
    )

    impact = analyzer.analyze(tmp_path, [ChangeRecord(path="src/auth.py")])

    assert impact.status is ImpactStatus.FULL_SUITE
    assert impact.reason == "coverage_unproven"


def test_historical_failure_is_added_only_after_coverage_is_proven(tmp_path: Path) -> None:
    for relative in ("src/auth.py", "tests/test_auth.py", "tests/test_login_flow.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    analyzer = ImpactAnalyzer(
        _index(
            ImpactEdge(
                source_path="tests/test_auth.py",
                target_path="src/auth.py",
                kind="covers",
            ),
            paths=["src/auth.py", "tests/test_auth.py", "tests/test_login_flow.py"],
        ),
        expected_worktree_hash=WORKTREE_HASH,
        trusted_full_suite=FULL_SUITE,
        historical_failures={"src/auth.py": ["tests/test_login_flow.py"]},
    )

    impact = analyzer.analyze(tmp_path, [ChangeRecord(path="src/auth.py")])

    assert impact.status is ImpactStatus.SELECTIVE
    assert impact.test_paths == ["tests/test_auth.py", "tests/test_login_flow.py"]
    assert impact.explanation["tests/test_login_flow.py"] == [
        "historical_failure:src/auth.py"
    ]
