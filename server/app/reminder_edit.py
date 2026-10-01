"""Reminder edit mode: one reminder, changed by voice from the watch's reminder screen.

Works like note edit mode (app/notes_edit.py): the watch keeps the microphone open and every sentence is
its own short turn (`listen_start` with `mode: "reminder"`). The assistant only edits that reminder: a
minimal prompt with the reminder's fields, the low-cost model, no history, no persona, no other tools, no
spoken reply. Silence costs nothing: only speech reaches the (billed) STT, and a turn that hears nothing
is not stored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db.models import Item
from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.items import _DONE, _END, _LOCATION, _NOTIFY, _PARTICIPANTS, device_full, is_done, local_time
from app.items import reminder_update_kwargs

REMINDER_EDIT_TOOL: dict[str, Any] = {
    "name": "reminder_edit",
    "description": "Apply the user's sentence to the reminder: change it, mark it completed, delete it, or undo "
    "the previous change.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["change", "delete", "undo"],
                "description": "change: give only the fields that change. delete: the whole reminder.",
            },
            "text": {"type": "string", "description": "What to do, at most 80 characters (keep it short)."},
            "due_local": {
                "type": "string",
                "description": "New start, local 'YYYY-MM-DD HH:MM'. A time range keeps its length unless "
                "end_local is given too.",
            },
            "end_local": _END,
            "notify_before_minutes": _NOTIFY,
            "location": _LOCATION,
            "participants": {
                **_PARTICIPANTS,
                "description": "The full new list of people (current ones kept unless the user removes them); "
                "an empty list removes them all.",
            },
            "done": _DONE,
        },
        "required": ["action"],
    },
}

REMINDER_SYSTEM = (
    "You edit ONE reminder on a smartwatch by voice. Each user message is one spoken sentence. Always answer "
    "with a single reminder_edit call:\n"
    "- Changes ('move it to 10:30', 'tomorrow at 9', 'until 11', 'add Ana', 'without Mihai', 'at the "
    "dentist', 'tell me 15 minutes before', 'call it ...') -> action change with only the fields that "
    "change. Resolve dates and times from 'Now'; keep the user's words and language in the text.\n"
    "- 'Done' / 'completed' -> done true; 'not done' / 'open it again' -> done false.\n"
    "- 'Delete it' / 'delete the reminder' -> action delete. Removing one detail ('remove the place', 'no "
    "advance notice') -> change with an empty value.\n"
    "- 'Undo' -> action undo.\n"
    "- Never answer questions or chat; you only edit this reminder. If a command is unclear, do not call "
    "the tool: reply with a very short question in the user's language, at most 60 characters."
)


def context(it: Item, tz: str) -> str:
    """The reminder as the model sees it (a second system message, after the cached REMINDER_SYSTEM)."""
    now = datetime.now(ZoneInfo(tz))
    fields = {
        "text": it.text,
        "due_local": local_time(it.due_at, tz),
        "end_local": local_time(it.end_at, tz),
        "notify_before_minutes": it.notify_before_min,
        "location": it.location,
        "participants": it.participants,
        "done": is_done(it),
    }
    return (
        f"Now: {now:%A %Y-%m-%d %H:%M} ({tz}).\n"
        f"Reminder #{it.number}: {json.dumps(fields, ensure_ascii=False)}"
    )


@dataclass
class EditResult:
    view: dict[str, Any] | None  # the reminder after the change (item_show); None when deleted
    deleted: bool = False


# One-step undo per (account, reminder number): the fields before the last change made in reminder mode.
_UNDO: dict[tuple[int, int], dict[str, Any]] = {}
_UNDO_MAX = 500


def _snapshot(it: Item) -> dict[str, Any]:
    return {
        "text": it.text,
        "due_at": it.due_at,
        "end_at": it.end_at,
        "notify_before_min": it.notify_before_min,
        "location": it.location,
        "participants": it.participants,
        "done": is_done(it),
    }


def _remember(account_id: int, number: int, before: dict[str, Any]) -> None:
    if len(_UNDO) >= _UNDO_MAX:
        _UNDO.pop(next(iter(_UNDO)))
    _UNDO[(account_id, number)] = before


def apply(account_id: int, number: int, arguments: str, tz: str) -> EditResult:
    """Apply one reminder_edit call. Raises ValueError (incl. ItemTimeError / ItemTextError) when it
    cannot be applied; nothing is changed then."""
    try:
        a = json.loads(arguments or "{}") or {}
    except ValueError:
        raise ValueError("arguments must be JSON") from None
    if not isinstance(a, dict):
        raise ValueError("arguments must be an object")
    action = a.get("action") or "change"
    with session_scope() as db:
        repo = ItemRepo(db)
        it = repo.get(account_id, "reminder", number)
        if it is None:
            raise ValueError(f"reminder #{number} does not exist")
        if action == "delete":
            repo.delete(it)
            _UNDO.pop((account_id, number), None)
            return EditResult(None, deleted=True)
        before = _snapshot(it)
        if action == "undo":
            previous = _UNDO.pop((account_id, number), None)
            if previous is None:
                raise ValueError("nothing to undo")
            it = repo.update(it, **previous)
        elif action == "change":
            changes = reminder_update_kwargs(a, tz)
            if not changes:
                raise ValueError("no change given")
            it = repo.update(it, **changes)
            _remember(account_id, number, before)
        else:
            raise ValueError(f"unknown action {action}")
        return EditResult(device_full(it, tz))
