"""Stable failure fingerprints with hostile stack input normalization."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from urllib.parse import urlsplit


class FingerprintInputRejected(ValueError):
    pass


_TRACEBACK_FRAME = re.compile(
    r'File ["\'](?P<file>[^"\']+)["\'], line (?P<line>\d+), in (?P<function>[A-Za-z0-9_<>.]+)'
)
_FUNCTION = re.compile(r"^[A-Za-z0-9_<>.]{1,256}$")
_REPOSITORY_MARKERS = ("/repository/", "/repo/")


def failure_fingerprint(
    *,
    repo_id: str,
    revision: str,
    pipeline: str,
    step: str,
    exception_type: str,
    frames: Sequence[Mapping[str, object]],
    schema: Mapping[str, object],
) -> str:
    normalized = {
        "repo_id": repo_id,
        "revision_family": _revision_family(revision),
        "pipeline": pipeline,
        "step": step,
        "exception_type": exception_type,
        "top_frames": list(frames[:8]),
        "schema_hash": schema_fingerprint(schema),
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def schema_fingerprint(schema: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            schema,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise FingerprintInputRejected("schema must contain JSON values") from exc
    return hashlib.sha256(encoded).hexdigest()


def normalize_stack_frames(raw: object) -> tuple[dict[str, object], ...]:
    candidates: list[Mapping[str, object]] = []
    if isinstance(raw, str):
        if len(raw) > 65_536:
            raise FingerprintInputRejected("stack frame text is too large")
        candidates = [match.groupdict() for match in _TRACEBACK_FRAME.finditer(raw)]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        if len(raw) > 64:
            raise FingerprintInputRejected("too many stack frames")
        for value in raw:
            if not isinstance(value, Mapping):
                raise FingerprintInputRejected("stack frame must be an object")
            candidates.append(value)
    else:
        raise FingerprintInputRejected("stack frame collection is invalid")

    if not candidates:
        raise FingerprintInputRejected("stack frame collection is empty")

    normalized: list[dict[str, object]] = []
    for candidate in candidates[:8]:
        file_value = candidate.get("file")
        function_value = candidate.get("function")
        line_value = candidate.get("line")
        if not isinstance(file_value, str) or not isinstance(function_value, str):
            raise FingerprintInputRejected("stack frame fields are invalid")
        try:
            line = int(line_value)
        except (TypeError, ValueError) as exc:
            raise FingerprintInputRejected("stack frame line is invalid") from exc
        if not 1 <= line <= 10_000_000 or not _FUNCTION.fullmatch(function_value):
            raise FingerprintInputRejected("stack frame fields are invalid")
        normalized.append(
            {
                "file": _repository_path(file_value),
                "function": function_value,
                "line": line,
            }
        )
    return tuple(normalized)


def _repository_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if (
        not normalized
        or len(normalized) > 2_048
        or "\x00" in normalized
        or urlsplit(normalized).scheme
    ):
        raise FingerprintInputRejected("stack frame path is invalid")
    if normalized.startswith("/"):
        for marker in _REPOSITORY_MARKERS:
            if marker in normalized:
                normalized = normalized.split(marker, 1)[1]
                break
        else:
            raise FingerprintInputRejected("stack frame path is outside the repository")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise FingerprintInputRejected("stack frame path is outside the repository")
    return path.as_posix()


def _revision_family(revision: str) -> str:
    normalized = revision.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40,64}", normalized):
        return normalized
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", revision):
        raise FingerprintInputRejected("revision is invalid")
    return revision
