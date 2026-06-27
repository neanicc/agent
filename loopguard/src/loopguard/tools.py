from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

READ_FILE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file from disk and return its contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filesystem path to read."}
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}


LIST_DIR_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "List the files and folders in a directory on disk.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default '.')."}
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}


def _resolve(path: str, root: str | Path) -> tuple[Path, str | None]:
    base = Path(root).resolve()
    target = (base / (path or ".")).resolve()
    if base != target and base not in target.parents:
        return target, f"Error: path {path!r} escapes the allowed directory."
    return target, None


def read_file(path: str, *, root: str | Path = ".") -> str:
    target, err = _resolve(path, root)
    if err:
        return err
    if not target.exists():
        return f"Error: {path} not found."
    if target.is_dir():
        return f"Error: {path} is a directory, not a file."
    try:
        return target.read_text(encoding="utf-8")[:4000]
    except Exception as exc:  # noqa: BLE001 - surface real read failures to the agent
        return f"Error: could not read {path}: {exc}"


def list_dir(path: str = ".", *, root: str | Path = ".") -> str:
    target, err = _resolve(path, root)
    if err:
        return err
    if not target.exists():
        return f"Error: {path} not found."
    if not target.is_dir():
        return f"Error: {path} is not a directory."
    try:
        entries = sorted(
            (p.name + ("/" if p.is_dir() else "")) for p in target.iterdir()
        )
    except Exception as exc:  # noqa: BLE001 - surface real read failures to the agent
        return f"Error: could not list {path}: {exc}"
    return "\n".join(entries[:200]) if entries else "(empty directory)"


TOOLS: dict[str, Callable[..., str]] = {"read_file": read_file, "list_dir": list_dir}
