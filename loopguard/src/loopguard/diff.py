from __future__ import annotations

import difflib


def unified_diff(a: str, b: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            a.splitlines(), b.splitlines(), fromfile="first", tofile="last", lineterm=""
        )
    )
