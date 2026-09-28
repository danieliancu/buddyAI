"""TurnContext: everything that belongs to one turn (user utterance -> reply)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.device_settings import DeviceSettings
from app.pipeline.metrics import TurnMarks
from app.providers.base import UsageItem


class TurnIO(Protocol):
    """What the pipeline may do towards the device. Implemented by the device connection."""

    async def send(self, turn: "TurnContext", type_: str, **fields: Any) -> bool: ...

    async def send_audio(self, turn: "TurnContext", opus_packet: bytes) -> bool: ...


@dataclass
class TurnContext:
    turn_id: int
    session_id: str
    device_id: str
    language: str
    settings: DeviceSettings
    downlink_rate: int
    db_id: int | None = None
    conversation_id: int | None = None
    marks: TurnMarks = field(default_factory=TurnMarks)
    # (pcm16k, arrival monotonic ms) items; None = uplink ended
    audio_in: asyncio.Queue = field(default_factory=asyncio.Queue)
    listening: bool = True
    cancelled: bool = False
    downlink_seq: int = 0
    task: asyncio.Task | None = None
    user_text: str = ""
    assistant_text: str = ""
    usage: list[UsageItem] = field(default_factory=list)


@dataclass
class TurnResult:
    status: str  # completed | no_speech | error | aborted
    error_code: str | None = None
    error_message: str | None = None
