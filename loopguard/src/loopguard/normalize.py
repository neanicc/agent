from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .config import LoopGuardConfig
from .event import LoopEvent

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HASH = re.compile(r"\b[a-f0-9]{32,}\b", re.I)
_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}[tT ][\d:.+-]+Z?\b")
_ABSOLUTE_PREFIX = re.compile(r"(?<!\w)(?:/workspace/[^\s\']+/|[A-Za-z]:\\)")
# Safety net: redact common provider API keys / bearer tokens that appear in free text,
# so secrets are never sent to the judge provider or written to the run log.
_SECRET = re.compile(
    r"\b(?:(?:sk|csk|pk|rk|ghp|gho|ghs|xoxb|xoxp)[-_][A-Za-z0-9_\-]{8,})"
    r"|(?:(?:AKIA|AIza)[A-Za-z0-9_\-]{12,})\b",
    re.I,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")
_WS = re.compile(r"\s+")


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _clean_text(value: str) -> str:
    value = _BEARER.sub("bearer <redacted>", value)
    value = _SECRET.sub("<redacted>", value)
    value = _UUID.sub("<uuid>", value)
    value = _HASH.sub("<hash>", value)
    value = _ISO.sub("<timestamp>", value)
    value = _ABSOLUTE_PREFIX.sub("<path>/", value)
    value = _WS.sub(" ", value).strip().lower()
    return value[:2000]


def _sanitize(value: Any, config: LoopGuardConfig) -> Any:
    if isinstance(value, dict):
        out = {}
        for key in sorted(value):
            low = str(key).lower()
            if low in config.ignored_arg_keys:
                continue
            if any(secret in low for secret in config.redact_keys):
                out[key] = "<redacted>"
            else:
                out[key] = _sanitize(value[key], config)
        return out
    if isinstance(value, list):
        return [_sanitize(v, config) for v in value[:50]]
    if isinstance(value, str):
        return _clean_text(value)
    return value


def normalize_event(event: LoopEvent, config: LoopGuardConfig | None = None) -> str:
    config = config or LoopGuardConfig()
    payload: dict[str, Any] = {"kind": event.kind, "agent": event.agent.lower()}
    if event.tool_name:
        payload["tool_name"] = event.tool_name.lower()
    if event.tool_args:
        payload["tool_args"] = _sanitize(event.tool_args, config)
    if event.input_text:
        payload["input_text"] = _clean_text(event.input_text)
    if event.output_text:
        payload["output_text"] = _clean_text(event.output_text)
    if event.error:
        payload["error"] = _clean_text(event.error)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def exact_signature(event: LoopEvent, config: LoopGuardConfig | None = None) -> str:
    return stable_hash(normalize_event(event, config))
