from __future__ import annotations

import stat
import tempfile
from pathlib import Path

import pytest

from loopguard.context.index import SymbolIndex
from loopguard.context.symbols import SymbolExtractor, SymbolSnapshot


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/context"
PARSER_CACHE = Path(tempfile.gettempdir()) / "loopguard-tree-sitter-test-cache-v1"


def test_extracts_python_definitions_imports_and_tests() -> None:
    extractor = SymbolExtractor()
    service = extractor.extract(
        "src/service.py",
        (FIXTURES / "python_repo/src/service.py").read_text(),
    )
    tests = extractor.extract(
        "tests/service_spec.py",
        (FIXTURES / "python_repo/tests/service_spec.py").read_text(),
    )

    assert service.definitions == ["load_user"]
    assert "app.models.User" in service.imports
    assert tests.test_names == ["test_load_user"]
    assert service.complete is True


def test_python_relative_imports_and_snapshot_completeness_are_unambiguous() -> None:
    snapshot = SymbolExtractor().extract(
        "package/service.py",
        "from . import models\nfrom ..shared import User\n",
    )

    assert snapshot.imports == ["..shared.User", ".models"]
    with pytest.raises(ValueError, match="complete snapshot"):
        SymbolSnapshot(path="src/a.py", language="python", error="syntax_error")
    with pytest.raises(ValueError, match="incomplete snapshot"):
        SymbolSnapshot(path="src/a.py", language="python", complete=False)


def test_extracts_typescript_and_tsx_without_a_model() -> None:
    PARSER_CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    extractor = SymbolExtractor(parser_cache=PARSER_CACHE)
    service = extractor.extract(
        "src/service.ts",
        (FIXTURES / "typescript_repo/src/service.ts").read_text(),
    )
    tests = extractor.extract(
        "src/service.test.ts",
        (FIXTURES / "typescript_repo/src/service.test.ts").read_text(),
    )
    tsx = extractor.extract(
        "src/component.tsx",
        'import React from "react"; export function Card(){ return <div />; }',
    )

    assert service.definitions == ["UserService", "loadUser"]
    assert "./models.User" in service.imports
    assert "./models.UserRole" in service.imports
    assert tests.test_names == ["describe:loadUser", "test:loads a user"]
    assert tsx.definitions == ["Card"]
    assert service.complete is tests.complete is tsx.complete is True


def test_unknown_syntax_error_and_oversized_sources_are_bounded() -> None:
    extractor = SymbolExtractor(max_source_bytes=64, parser_cache=PARSER_CACHE)

    unknown = extractor.extract("src/data.rb", "def work; end")
    invalid_python = extractor.extract("src/broken.py", "def broken(")
    oversized = extractor.extract("src/large.py", "x" * 65)

    assert unknown.language == "unknown" and unknown.definitions == []
    assert unknown.complete is True
    assert invalid_python.complete is False and invalid_python.error == "syntax_error"
    assert oversized.complete is False and oversized.error == "source_too_large"


def test_symbol_index_versions_changed_files_import_edges_renames_and_deletes(
    tmp_path: Path,
) -> None:
    extractor = SymbolExtractor()
    index = SymbolIndex(tmp_path / "symbols.db")
    first = extractor.extract("src/service.py", "import app.models\ndef load(): pass\n")

    assert index.update("repo", "worktree", first, content_hash="a" * 64) is True
    assert index.update("repo", "worktree", first, content_hash="a" * 64) is False
    assert index.paths_for_symbol("repo", "load") == ["src/service.py"]
    assert index.imports_for_path("repo", "src/service.py") == ["app.models"]
    assert index.version("repo", "worktree", "src/service.py") == 1

    changed = extractor.extract("src/service.py", "def refresh(): pass\n")
    assert index.update("repo", "worktree", changed, content_hash="b" * 64) is True
    assert index.paths_for_symbol("repo", "load") == []
    assert index.paths_for_symbol("repo", "refresh") == ["src/service.py"]
    assert index.version("repo", "worktree", "src/service.py") == 2

    index.rename("repo", "worktree", "src/service.py", "src/renamed.py")
    assert index.paths_for_symbol("repo", "refresh") == ["src/renamed.py"]
    index.delete("repo", "worktree", "src/renamed.py")
    assert index.paths_for_symbol("repo", "refresh") == []
    index.close()
    assert stat.S_IMODE((tmp_path / "symbols.db").stat().st_mode) == 0o600


def test_index_scopes_queries_by_worktree_and_persists_renames(tmp_path: Path) -> None:
    database = tmp_path / "symbols.db"
    extractor = SymbolExtractor()
    first = extractor.extract("src/service.py", "def shared(): pass\n")
    second = extractor.extract("src/other.py", "def shared(): pass\n")
    occupied = extractor.extract("src/renamed.py", "def occupied(): pass\n")

    with SymbolIndex(database) as index:
        index.update("repo", "one", first, content_hash="a" * 64)
        index.update("repo", "two", second, content_hash="b" * 64)
        index.update("repo", "two", occupied, content_hash="c" * 64)
        assert index.paths_for_symbol("repo", "shared", worktree_id="one") == [
            "src/service.py"
        ]
        index.rename("repo", "one", "src/service.py", "src/renamed.py")

    with SymbolIndex(database) as reopened:
        assert reopened.paths_for_symbol("repo", "shared", worktree_id="one") == [
            "src/renamed.py"
        ]
        assert reopened.paths_for_symbol("repo", "shared", worktree_id="two") == [
            "src/other.py"
        ]
        with pytest.raises(ValueError, match="already exists"):
            reopened.rename("repo", "two", "src/other.py", "src/renamed.py")


def test_incomplete_reindex_removes_stale_symbols(tmp_path: Path) -> None:
    extractor = SymbolExtractor()
    index = SymbolIndex(tmp_path / "symbols.db")
    valid = extractor.extract("src/service.py", "def load(): pass\n")
    invalid = extractor.extract("src/service.py", "def load(")

    index.update("repo", "worktree", valid, content_hash="a" * 64)
    index.update("repo", "worktree", invalid, content_hash="b" * 64)

    assert index.paths_for_symbol("repo", "load") == []
    assert index.snapshot("repo", "worktree", "src/service.py").complete is False
