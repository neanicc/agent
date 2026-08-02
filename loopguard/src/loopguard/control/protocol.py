from __future__ import annotations

import asyncio
import json
import re
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


MAGIC = b"LGCP"
PROTOCOL_VERSION = 1
DEFAULT_MAX_FRAME_BYTES = 1_048_576
MAX_REQUEST_ID_BYTES = 128

_PRELUDE = struct.Struct("!4sBBHI")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class MessageType(IntEnum):
    EVENT = 1
    ACK = 2
    ERROR = 3


class ProtocolError(Exception):
    """A framed message failed the public local-protocol contract."""

    def __init__(self, code: str, *, request_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class Frame:
    version: int
    message_type: MessageType
    request_id: str
    payload: dict[str, Any]


def encode_frame(
    message_type: MessageType,
    *,
    request_id: str,
    payload: dict[str, Any],
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> bytes:
    if not isinstance(message_type, MessageType):
        raise ProtocolError("unsupported_message_type")
    request_bytes = _encode_request_id(request_id)
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_payload", request_id=request_id)
    try:
        body = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProtocolError("invalid_payload", request_id=request_id) from exc
    if len(body) > max_frame_bytes:
        raise ProtocolError("frame_too_large", request_id=request_id)
    return _PRELUDE.pack(
        MAGIC,
        PROTOCOL_VERSION,
        int(message_type),
        len(request_bytes),
        len(body),
    ) + request_bytes + body


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> Frame | None:
    if max_frame_bytes <= 0:
        raise ValueError("max_frame_bytes must be positive")
    prelude = await _read_prelude(reader)
    if prelude is None:
        return None

    magic, version, raw_message_type, request_length, body_length = _PRELUDE.unpack(prelude)
    if magic != MAGIC:
        raise ProtocolError("invalid_magic")
    if version != PROTOCOL_VERSION:
        raise ProtocolError("unsupported_protocol_version")
    try:
        message_type = MessageType(raw_message_type)
    except ValueError as exc:
        raise ProtocolError("unsupported_message_type") from exc
    if request_length == 0 or request_length > MAX_REQUEST_ID_BYTES:
        raise ProtocolError("invalid_request_id")
    if body_length > max_frame_bytes:
        raise ProtocolError("frame_too_large")

    request_bytes = await _read_exactly(reader, request_length)
    try:
        request_id = request_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_request_id") from exc
    if _REQUEST_ID.fullmatch(request_id) is None:
        raise ProtocolError("invalid_request_id")

    body = await _read_exactly(reader, body_length, request_id=request_id)
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_utf8", request_id=request_id) from exc
    try:
        payload = json.loads(
            decoded,
            parse_constant=lambda _value: _reject_json_constant(),
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError("invalid_json", request_id=request_id) from exc
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_payload", request_id=request_id)
    return Frame(
        version=version,
        message_type=message_type,
        request_id=request_id,
        payload=payload,
    )


def _encode_request_id(request_id: str) -> bytes:
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise ProtocolError("invalid_request_id")
    return request_id.encode("ascii")


async def _read_prelude(reader: asyncio.StreamReader) -> bytes | None:
    try:
        return await reader.readexactly(_PRELUDE.size)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise ProtocolError("truncated_frame") from exc


async def _read_exactly(
    reader: asyncio.StreamReader,
    length: int,
    *,
    request_id: str | None = None,
) -> bytes:
    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("truncated_frame", request_id=request_id) from exc


def _reject_json_constant() -> None:
    raise ValueError("non-finite JSON number")
