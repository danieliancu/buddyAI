"""Wire format helpers for PROTOCOL.md v1 (envelope + 12-byte binary audio header)."""

from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1

KIND_UPLINK = 0x01
KIND_DOWNLINK = 0x02
CODEC_OPUS = 0

_HEADER = struct.Struct(">BBHII")
HEADER_SIZE = _HEADER.size  # 12


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class AudioFrame:
    kind: int
    turn_id: int
    frame_seq: int
    payload: bytes
    flags: int = 0
    codec: int = CODEC_OPUS

    def pack(self) -> bytes:
        return _HEADER.pack(self.kind, self.flags, self.codec, self.turn_id, self.frame_seq) + self.payload

    @classmethod
    def unpack(cls, data: bytes) -> "AudioFrame":
        if len(data) < HEADER_SIZE:
            raise ValueError("audio frame shorter than header")
        kind, flags, codec, turn_id, seq = _HEADER.unpack_from(data)
        return cls(kind=kind, turn_id=turn_id, frame_seq=seq, payload=bytes(data[HEADER_SIZE:]), flags=flags, codec=codec)


class ProtocolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Envelope:
    """Builds outgoing envelopes with a per-session monotonic sequence number."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self._seq = 0

    def build(self, type_: str, turn_id: int | None = None, **fields: Any) -> str:
        self._seq += 1
        msg = {
            "type": type_,
            "protocol_version": PROTOCOL_VERSION,
            "session_id": self.session_id,
            "turn_id": turn_id,
            "sequence_number": self._seq,
            "timestamp": now_ms(),
        }
        msg.update(fields)
        return json.dumps(msg, ensure_ascii=False, separators=(",", ":"))


def parse_message(text: str) -> dict[str, Any]:
    try:
        msg = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("bad_request", f"invalid JSON: {exc}") from exc
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("bad_request", "missing type")
    version = msg.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ProtocolError("protocol_unsupported", f"protocol_version {version!r} not supported")
    turn_id = msg.get("turn_id")
    if turn_id is not None and (not isinstance(turn_id, int) or not 0 <= turn_id <= 0xFFFFFFFF):
        raise ProtocolError("bad_request", "turn_id must be uint32")
    return msg
