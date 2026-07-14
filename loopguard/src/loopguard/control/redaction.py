from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


REDACTED = "[REDACTED]"

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "aws_secret_access_key",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "id_token",
    "password",
    "passwd",
    "private_key",
    "proxy_authorization",
    "pwd",
    "refresh_token",
    "secret",
    "secret_key",
    "session_cookie",
    "set_cookie",
    "token",
}
_SECRET_KEY_SUFFIXES = (
    "_api_key",
    "_cookie",
    "_credential",
    "_credentials",
    "_password",
    "_passwd",
    "_private_key",
    "_secret",
    "_secret_key",
    "_token",
)

_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.DOTALL,
)
_URL_USERINFO = re.compile(
    r"(?i)\b(?P<scheme>[a-z][a-z0-9+.-]*://)"
    r"[^/\s:@]+(?::[^/@\s]*)?@"
)
_AUTHORIZATION = re.compile(
    r"(?i)\b(?P<prefix>(?:bearer|basic)\s+)"
    r"(?P<quote>['\"]?)[A-Za-z0-9+/_=.-]{8,}(?P=quote)"
)
_ASSIGNMENT = re.compile(
    r"(?i)\b(?P<prefix>(?:"
    r"x-api-key|api[._-]?key|openai_api_key|anthropic_api_key|"
    r"access_token|refresh_token|client_secret|token|password|passwd|secret"
    r")\s*[:=]\s*)"
    r"(?P<quote>['\"]?)[^\s'\";&|]{8,}(?P=quote)"
)
_GITHUB_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_JWT = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_OPENAI_STYLE_KEY = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9._-]{8,}\b")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_KEY_CHARACTER = re.compile(r"[^a-z0-9]+")


def redact(value: Any) -> Any:
    """Return an independent copy with likely credentials replaced deterministically."""
    return _redact(value, ancestors=set())


def _redact(value: Any, *, ancestors: set[int]) -> Any:
    if isinstance(value, dict):
        identity = _enter_container(value, ancestors)
        try:
            return {
                key: REDACTED if _is_secret_key(key) else _redact(child, ancestors=ancestors)
                for key, child in value.items()
            }
        finally:
            ancestors.remove(identity)
    if isinstance(value, list):
        identity = _enter_container(value, ancestors)
        try:
            return [_redact(child, ancestors=ancestors) for child in value]
        finally:
            ancestors.remove(identity)
    if isinstance(value, tuple):
        identity = _enter_container(value, ancestors)
        try:
            return tuple(_redact(child, ancestors=ancestors) for child in value)
        finally:
            ancestors.remove(identity)
    if isinstance(value, str):
        return _redact_string(value)
    return deepcopy(value)


def _enter_container(value: object, ancestors: set[int]) -> int:
    identity = id(value)
    if identity in ancestors:
        raise ValueError("control payload contains a cyclic container")
    ancestors.add(identity)
    return identity


def _is_secret_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    with_snake_case_boundaries = _CAMEL_BOUNDARY.sub("_", key)
    normalized = _NON_KEY_CHARACTER.sub("_", with_snake_case_boundaries.lower()).strip("_")
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_KEY_SUFFIXES)


def _redact_string(value: str) -> str:
    result = _PRIVATE_KEY.sub(REDACTED, value)
    result = _URL_USERINFO.sub(lambda match: f"{match.group('scheme')}{REDACTED}@", result)
    result = _AUTHORIZATION.sub(_preserve_prefix, result)
    result = _ASSIGNMENT.sub(_preserve_prefix, result)
    result = _GITHUB_TOKEN.sub(REDACTED, result)
    result = _AWS_ACCESS_KEY.sub(REDACTED, result)
    result = _JWT.sub(REDACTED, result)
    return _OPENAI_STYLE_KEY.sub(REDACTED, result)


def _preserve_prefix(match: re.Match[str]) -> str:
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}{REDACTED}{quote}"
