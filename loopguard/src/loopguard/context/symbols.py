from __future__ import annotations

import ast
import os
import threading
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SymbolSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    language: Literal["python", "typescript", "tsx", "unknown"]
    definitions: list[str] = Field(default_factory=list, max_length=10_000)
    imports: list[str] = Field(default_factory=list, max_length=10_000)
    test_names: list[str] = Field(default_factory=list, max_length=10_000)
    complete: bool = True
    error: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value or "\\" in value or "\x00" in value:
            raise ValueError("symbol path must be repository-relative POSIX form")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("symbol path escapes the repository")
        return path.as_posix()

    @field_validator("definitions", "imports", "test_names")
    @classmethod
    def unique_sorted(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 1024 or "\x00" in value for value in values):
            raise ValueError("symbol values must be bounded and non-empty")
        if values != sorted(set(values)):
            raise ValueError("symbol values must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_completeness(self) -> SymbolSnapshot:
        if self.complete and self.error is not None:
            raise ValueError("complete snapshot cannot contain an error")
        if not self.complete and self.error is None:
            raise ValueError("incomplete snapshot must contain an error")
        return self


_PARSER_LOCK = threading.RLock()
_CONFIGURED_CACHE: Path | None = None


class SymbolExtractor:
    def __init__(
        self,
        *,
        max_source_bytes: int = 2 * 1024 * 1024,
        max_nodes: int = 200_000,
        parser_cache: Path | None = None,
    ) -> None:
        if max_source_bytes <= 0 or max_nodes <= 0:
            raise ValueError("symbol extraction limits must be positive")
        self.max_source_bytes = max_source_bytes
        self.max_nodes = max_nodes
        configured = parser_cache or Path(
            os.environ.get(
                "LOOPGUARD_TREE_SITTER_CACHE",
                str(_default_parser_cache()),
            )
        )
        self.parser_cache = configured.expanduser().absolute()

    def extract(self, path: str, source: str) -> SymbolSnapshot:
        language = _language(path)
        empty = SymbolSnapshot(path=path, language=language)
        if not isinstance(source, str):
            raise TypeError("symbol source must be text")
        encoded = source.encode("utf-8")
        if len(encoded) > self.max_source_bytes:
            return empty.model_copy(update={"complete": False, "error": "source_too_large"})
        if language == "unknown":
            return empty
        if language == "python":
            return self._python(empty, source)
        return self._typescript(empty, encoded)

    def _python(self, empty: SymbolSnapshot, source: str) -> SymbolSnapshot:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError, MemoryError):
            return empty.model_copy(update={"complete": False, "error": "syntax_error"})
        definitions: set[str] = set()
        imports: set[str] = set()
        tests: set[str] = set()
        for visited, node in enumerate(ast.walk(tree), start=1):
            if visited > self.max_nodes:
                return empty.model_copy(update={"complete": False, "error": "node_limit"})
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definitions.add(node.name)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                    "test"
                ):
                    tests.add(node.name)
            elif isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                separator = "" if module.endswith(".") else "."
                imports.update(f"{module}{separator}{alias.name}" for alias in node.names)
        return _completed_snapshot(empty, definitions, imports, tests)

    def _typescript(self, empty: SymbolSnapshot, source: bytes) -> SymbolSnapshot:
        try:
            parser = _typescript_parser(empty.language, self.parser_cache)
            root = parser.parse(source).root_node
        except Exception:
            return empty.model_copy(update={"complete": False, "error": "parser_unavailable"})
        if root.has_error:
            return empty.model_copy(update={"complete": False, "error": "syntax_error"})
        definitions: set[str] = set()
        imports: set[str] = set()
        tests: set[str] = set()
        stack = [root]
        visited = 0
        while stack:
            node = stack.pop()
            visited += 1
            if visited > self.max_nodes:
                return empty.model_copy(update={"complete": False, "error": "node_limit"})
            node_type = node.type
            if node_type in {
                "class_declaration",
                "enum_declaration",
                "function_declaration",
                "interface_declaration",
                "method_definition",
                "type_alias_declaration",
            }:
                name = node.child_by_field_name("name")
                if name is not None:
                    definitions.add(_node_text(name, source))
            elif node_type == "variable_declarator":
                value = node.child_by_field_name("value")
                name = node.child_by_field_name("name")
                if (
                    value is not None
                    and value.type in {"arrow_function", "function_expression"}
                    and name is not None
                ):
                    definitions.add(_node_text(name, source))
            elif node_type == "import_statement":
                imports.update(_typescript_imports(node, source))
            elif node_type == "call_expression":
                test_name = _typescript_test_name(node, source)
                if test_name is not None:
                    tests.add(test_name)
            stack.extend(reversed(node.children))
        return _completed_snapshot(empty, definitions, imports, tests)


def _language(path: str) -> Literal["python", "typescript", "tsx", "unknown"]:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".ts", ".js", ".mjs", ".cjs"}:
        return "typescript"
    if suffix in {".tsx", ".jsx"}:
        return "tsx"
    return "unknown"


def _default_parser_cache() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME")
    root = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return root / "loopguard" / "tree-sitter-v1"


def _completed_snapshot(
    empty: SymbolSnapshot,
    definitions: set[str],
    imports: set[str],
    tests: set[str],
) -> SymbolSnapshot:
    values = definitions | imports | tests
    if (
        any(not value or len(value) > 1024 or "\x00" in value for value in values)
        or len(definitions) > 10_000
        or len(imports) > 10_000
        or len(tests) > 10_000
    ):
        return empty.model_copy(update={"complete": False, "error": "symbol_limit"})
    return empty.model_copy(
        update={
            "definitions": sorted(definitions),
            "imports": sorted(imports),
            "test_names": sorted(tests),
        }
    )


def _typescript_parser(language: str, cache: Path):
    global _CONFIGURED_CACHE
    with _PARSER_LOCK:
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache.chmod(0o700)
        from tree_sitter_language_pack import PackConfig, configure, get_parser

        if _CONFIGURED_CACHE != cache:
            configure(PackConfig(cache_dir=str(cache)))
            _CONFIGURED_CACHE = cache
        return get_parser("tsx" if language == "tsx" else "typescript")


def _typescript_imports(node, source: bytes) -> set[str]:
    source_node = node.child_by_field_name("source")
    if source_node is None:
        return set()
    module = _strip_string(_node_text(source_node, source))
    names: set[str] = set()
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == "import_specifier":
            alias = current.child_by_field_name("alias")
            name = alias or current.child_by_field_name("name")
            if name is not None:
                names.add(_node_text(name, source))
        elif current.type in {"namespace_import", "namespace_import_clause"}:
            identifiers = [child for child in current.children if child.type == "identifier"]
            names.update(_node_text(child, source) for child in identifiers)
        stack.extend(current.children)
    import_clause = next((child for child in node.children if child.type == "import_clause"), None)
    if import_clause is not None:
        direct = [child for child in import_clause.children if child.type == "identifier"]
        names.update(_node_text(child, source) for child in direct)
    return {f"{module}.{name}" for name in names} if names else {module}


def _typescript_test_name(node, source: bytes) -> str | None:
    function = node.child_by_field_name("function")
    arguments = node.child_by_field_name("arguments")
    if function is None or arguments is None:
        return None
    call = _node_text(function, source).split(".")[0]
    if call not in {"describe", "it", "test"}:
        return None
    string = next((child for child in arguments.children if child.type == "string"), None)
    if string is None:
        return None
    return f"{call}:{_strip_string(_node_text(string, source))}"


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _strip_string(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] in {'"', "'", "`"} else value
