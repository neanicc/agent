from __future__ import annotations

import asyncio
import json

import pytest

from loopguard.control.protocol import (
    MAGIC,
    PROTOCOL_VERSION,
    Frame,
    MessageType,
    ProtocolError,
    encode_frame,
    read_frame,
)


async def _read_bytes(value: bytes, *, max_frame_bytes: int = 1_048_576) -> Frame:
    reader = asyncio.StreamReader()
    reader.feed_data(value)
    reader.feed_eof()
    frame = await read_frame(reader, max_frame_bytes=max_frame_bytes)
    assert frame is not None
    return frame


def test_protocol_round_trip_has_version_type_length_and_request_id():
    encoded = encode_frame(
        MessageType.EVENT,
        request_id="req-1",
        payload={"event_id": "evt-1", "safe": True},
    )
    frame = asyncio.run(_read_bytes(encoded))

    assert encoded.startswith(MAGIC + bytes([PROTOCOL_VERSION]))
    assert frame == Frame(
        version=1,
        message_type=MessageType.EVENT,
        request_id="req-1",
        payload={"event_id": "evt-1", "safe": True},
    )


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda raw: b"BAD!" + raw[4:], "invalid_magic"),
        (lambda raw: raw[:4] + b"\x7f" + raw[5:], "unsupported_protocol_version"),
        (lambda raw: raw[:5] + b"\x7f" + raw[6:], "unsupported_message_type"),
    ],
)
def test_protocol_rejects_unknown_header_semantics(mutator, code):
    raw = encode_frame(MessageType.EVENT, request_id="req", payload={"ok": True})

    with pytest.raises(ProtocolError) as raised:
        asyncio.run(_read_bytes(mutator(raw)))

    assert raised.value.code == code


def test_protocol_rejects_oversized_frame_before_reading_body():
    raw = encode_frame(MessageType.EVENT, request_id="req", payload={"value": "x" * 100})

    with pytest.raises(ProtocolError) as raised:
        asyncio.run(_read_bytes(raw, max_frame_bytes=16))

    assert raised.value.code == "frame_too_large"


@pytest.mark.parametrize(
    ("request_id", "payload"),
    [
        ("", {"ok": True}),
        ("contains space", {"ok": True}),
        ("x" * 129, {"ok": True}),
        ("req", ["not", "an", "object"]),
    ],
)
def test_encoder_rejects_invalid_request_ids_and_non_object_payloads(request_id, payload):
    with pytest.raises(ProtocolError):
        encode_frame(MessageType.EVENT, request_id=request_id, payload=payload)


def test_protocol_rejects_invalid_utf8_json_and_trailing_json():
    valid = encode_frame(MessageType.EVENT, request_id="req", payload={"ok": True})
    request_length = int.from_bytes(valid[6:8], "big")
    payload_length = int.from_bytes(valid[8:12], "big")
    body_offset = 12 + request_length

    invalid_utf8 = valid[:body_offset] + b"\xff" + valid[body_offset + 1 :]
    invalid_json_body = b"{" + b"x" * (payload_length - 1)
    invalid_json = valid[:body_offset] + invalid_json_body
    trailing_body = json.dumps({"ok": True}).encode() + b"{}"
    trailing = (
        valid[:8]
        + len(trailing_body).to_bytes(4, "big")
        + valid[12:body_offset]
        + trailing_body
    )

    for raw, expected in (
        (invalid_utf8, "invalid_utf8"),
        (invalid_json, "invalid_json"),
        (trailing, "invalid_json"),
    ):
        with pytest.raises(ProtocolError) as raised:
            asyncio.run(_read_bytes(raw))
        assert raised.value.code == expected


def test_protocol_rejects_truncated_frames_with_stable_code():
    raw = encode_frame(MessageType.EVENT, request_id="req", payload={"ok": True})

    with pytest.raises(ProtocolError) as raised:
        asyncio.run(_read_bytes(raw[:-1]))

    assert raised.value.code == "truncated_frame"


def test_protocol_rejects_non_finite_json_numbers():
    valid = encode_frame(MessageType.EVENT, request_id="req", payload={"ok": True})
    request_length = int.from_bytes(valid[6:8], "big")
    body_offset = 12 + request_length
    body = b'{"value":NaN}'
    raw = valid[:8] + len(body).to_bytes(4, "big") + valid[12:body_offset] + body

    with pytest.raises(ProtocolError) as raised:
        asyncio.run(_read_bytes(raw))

    assert raised.value.code == "invalid_json"
