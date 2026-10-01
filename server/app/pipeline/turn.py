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
    language: str  # "auto" until the transcript's language is detected, then an ISO 639-1 code
    settings: DeviceSettings
    downlink_rate: int
    account_id: int | None = None  # owner of the watch (None = unassigned/operator stock)
    fallback_language: str | None = None  # last detected language in this session (used when unsure)
    auto_language: bool = False
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
    items_changed: bool = False  # a tool created/changed/deleted a note or reminder
    settings_changed: bool = False  # a tool changed the watch settings (volume, language...)
    # What to open on the watch after the turn ends: {"list": kind} or {"item": {...}} (last one wins)
    pending_open: dict[str, Any] | None = None
    searcher: Any = None  # app.search.WebSearch for this turn (one search per turn)
    search_city: str = ""  # default location for web searches (from the watch's time zone)
    mode: str = "chat"  # "chat" | "note" (note edit mode, app/notes_edit.py)
    note_number: int | None = None  # note mode: the note being edited
    end_requested: bool = False  # the watch's stop button: transcribe what was said so far
    changed_line: int | None = None  # note mode: line to highlight on the watch

    def __post_init__(self) -> None:
        self.auto_language = self.language == "auto"


@dataclass
class TurnResult:
    status: str  # completed | no_speech | error | aborted
    error_code: str | None = None
    error_message: str | None = None
