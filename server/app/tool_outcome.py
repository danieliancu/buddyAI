"""The result of one assistant tool call (shared by the settings tool and the item tools)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolOutcome:
    result: str  # JSON text returned to the model
    changed: bool = False  # notes / reminders changed: push them to the watches when the turn ends
    # What to open on the watch after the reply: {"list": kind} | {"item": {...}}. Set after a change, or for
    # a list / item the user asked to see - never for an internal lookup.
    open: dict[str, Any] | None = None
    settings_changed: bool = False  # watch settings changed: push them when the turn ends
    awaits_answer: bool = False  # the reply asks the user something the operation needs (clarify / confirm)
    open_uid: str | None = None  # the item opened on the watch (the default target for "this", "move it")
