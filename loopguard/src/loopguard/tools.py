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


def read_file(path: str, *, root: str | Path = ".") -> str:
    base = Path(root).resolve()
    target = (base / path).resolve()
    if base != target and base not in target.parents:
        return f"Error: path {path!r} escapes the allowed directory."
    if not target.exists():
        return f"Error: {path} not found."
    if target.is_dir():
        return f"Error: {path} is a directory, not a file."
    try:
        return target.read_text(encoding="utf-8")[:4000]
    except Exception as exc:  # noqa: BLE001 - surface real read failures to the agent
        return f"Error: could not read {path}: {exc}"


TOOLS: dict[str, Callable[..., str]] = {"read_file": read_file}
