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
from app.db.repositories import ItemConflictError, ItemRepo
from app.db.session import session_scope
from app.items import _DONE, _END, _LOCATION, _NOTIFY, _PARTICIPANTS, device_full, is_done, local_time
from app.notes_edit import IGNORE

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
            "add_participants": {"type": "array", "items": {"type": "string"}, "description": "people to add"},
            "remove_participants": {"type": "array", "items": {"type": "string"}, "description": "people to remove"},
            "remove": {
                "type": "array",
                "items": {"type": "string", "enum": ["location", "participants", "end", "notify"]},
                "description": "details to remove entirely",
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
    "change. Resolve dates and times from 'Now'; keep the user's words and language in the text. Change the "
    "text only when the user clearly asks for it ('call it ...', 'change the text to ...', 'rename it ...', "
    "'it's about ...'): never make a stray word or sentence the new text.\n"
    "- 'Done' / 'completed' -> done true; 'not done' / 'open it again' -> done false.\n"
    "- 'Delete it' / 'delete the reminder' -> action delete. Removing a detail ('remove the place', 'no advance "
    "notice', 'without Mihai') -> change with remove / remove_participants. Adding a person -> add_participants "
    "(the others stay).\n"
    "- If the user talks about a DIFFERENT reminder or note than this one, do not call the tool: reply only "
    "OTHER.\n"
    "- 'Undo' -> action undo.\n"
    "- The microphone stays open, so it also hears speech that is not an instruction about this reminder: a "
    "lone word ('love', 'impossible'), a remark, someone talking to the user, a TV or radio, words in another "
    f"language. For that, do not call the tool: reply only {IGNORE}.\n"
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
    view: dict[str, Any] | None = None  # the reminder after the change (item_show); None when deleted
    deleted: bool = False
    version: int | None = None  # the reminder's version after the change


# One-step undo per (account, reminder uid): the fields before the last change made in reminder mode, and
# the version that change produced - undo only applies while the reminder is still at that version (it never
# overwrites a change made elsewhere meanwhile).
_UNDO: dict[tuple[int, str], tuple[dict[str, Any], int]] = {}
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


def _remember(account_id: int, uid: str, before: dict[str, Any], version: int) -> None:
    if len(_UNDO) >= _UNDO_MAX:
        _UNDO.pop(next(iter(_UNDO)))
    _UNDO[(account_id, uid)] = (before, version)


def apply(account_id: int, uid: str, version: int, arguments: str, tz: str, op: Any = None) -> EditResult:
    """Apply one reminder_edit call to the reminder `uid`, which must still be at `version` (it was read
    before the model ran). On the reminder screen everything applies at once, deletions too. Raises
    ValueError (incl. ItemTimeError / ItemTextError / ItemTimeAsk) when it cannot be applied, and
    ItemConflictError when the reminder changed meanwhile; nothing is changed then."""
    from app.voice_tools import reminder_changes

    try:
        a = json.loads(arguments or "{}") or {}
    except ValueError:
        raise ValueError("arguments must be JSON") from None
    if not isinstance(a, dict):
        raise ValueError("arguments must be an object")
    action = a.get("action") or "change"
    with session_scope() as db:
        repo = ItemRepo(db)
        it = repo.get_by_uid(account_id, uid)
        if it is None:
            raise ItemConflictError(missing=[uid])
        if it.version != version:
            raise ItemConflictError(changed=[uid])
        if action == "delete":
            repo.delete_exact(account_id, [(uid, version)], op=op)
            _UNDO.pop((account_id, uid), None)
            return EditResult(None, deleted=True)
        before = _snapshot(it)
        if action == "undo":
            previous = _UNDO.get((account_id, uid))
            if previous is None or previous[1] != it.version:
                raise ValueError("nothing to undo")
            _UNDO.pop((account_id, uid), None)
            it = repo.update(it, **previous[0], expected_version=version, op=op)
        elif action == "change":
            changes = {k: v for k, v in a.items() if k != "action"}
            kw, _removed = reminder_changes(it, changes, tz)
            if not kw:
                raise ValueError("no change given")
            it = repo.update(it, **kw, expected_version=version, op=op)
            _remember(account_id, uid, before, it.version)
        else:
            raise ValueError(f"unknown action {action}")
        return EditResult(device_full(it, tz), version=it.version)
