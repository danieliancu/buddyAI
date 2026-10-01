"""Notes and reminders: views for the watch and the web app, and the assistant's function tools.

Notes are text only (up to 10000 characters). Reminders have a due time, an optional end time (a
range such as 09:30-10:00), an optional advance notice (notify_before_min: an extra alert that many
minutes before the start, besides the one at the start), an optional location and participants (taken
from the text by the assistant) and a short text (up to 80 characters); once the time has passed they are "overdue" until completed, deleted or rescheduled. A
completed reminder (`done`) no longer fires or counts as overdue. Items belong to the account; numbers are per (account, kind) and a new item takes the
lowest free number (see ItemRepo). The watch gets a light snapshot (`items`); a note's full text is
sent when it is opened (`item_show`), a due reminder with `reminder_fire`.
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app import settings_tool
from app.db.models import Item
from app.db.repositories import ItemLimitError, ItemRepo, ItemTextError
from app.db.session import session_scope
from app.settings_tool import SETTINGS_RULE, SETTINGS_TOOL

log = logging.getLogger(__name__)

KINDS = ItemRepo.KINDS
PREVIEW_CHARS = 120  # about two lines on the watch
LOCAL_FMT = "%Y-%m-%d %H:%M"


# --- views -----------------------------------------------------------------------------------


def local_time(dt: datetime | None, tz: str) -> str | None:
    return dt.astimezone(ZoneInfo(tz)).strftime(LOCAL_FMT) if dt else None


def parse_local(value: str, tz: str) -> datetime:
    """'YYYY-MM-DD HH:MM' (or ISO 8601, with or without offset) in `tz` -> aware UTC datetime."""
    dt = datetime.fromisoformat(value.strip().replace("T", " "))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc)


_CLOCK = re.compile(r"\d{1,2}:\d{2}")


def parse_local_time(value: Any, tz: str, field: str) -> datetime:
    """Like parse_local, for the assistant's tools: the value must carry a time of day. A date alone
    ('2030-05-01') would silently become midnight, so it is refused and the model has to ask."""
    text = str(value or "").strip()
    if not _CLOCK.search(text):
        raise ValueError(
            f"{field} needs a time of day ('YYYY-MM-DD HH:MM'). Do not guess one: ask the user what time "
            "(at least the start time)."
        )
    return parse_local(text, tz)


def preview(text: str) -> str:
    """Start of a note on one line (line breaks become spaces), shortened for list rows."""
    flat = " ".join(text.split())
    return flat[:PREVIEW_CHARS].rstrip() + ("…" if len(flat) > PREVIEW_CHARS else "")


def is_done(it: Item) -> bool:
    return it.kind == "reminder" and it.done_at is not None


def is_overdue(it: Item, now: datetime | None = None) -> bool:
    return (
        it.kind == "reminder"
        and not is_done(it)
        and it.due_at is not None
        and it.due_at <= (now or datetime.now(timezone.utc))
    )


def web_view(it: Item) -> dict[str, Any]:
    return {
        "kind": it.kind,
        "number": it.number,
        "text": it.text,
        "due_at": it.due_at.isoformat() if it.due_at else None,
        "end_at": it.end_at.isoformat() if it.end_at else None,
        "notify_before_min": it.notify_before_min,
        "location": it.location,
        "participants": it.participants,
        "overdue": is_overdue(it),
        "done": is_done(it),
        "created_at": it.created_at.isoformat(),
        "updated_at": it.updated_at.isoformat(),
    }


def device_full(it: Item, tz: str) -> dict[str, Any]:
    """One item with its full text, for `item_show` / `reminder_fire`."""
    out: dict[str, Any] = {"kind": it.kind, "number": it.number, "text": it.text}
    if it.kind == "reminder":
        out["due_local"] = local_time(it.due_at, tz)
        out["end_local"] = local_time(it.end_at, tz)
        out["notify_before"] = it.notify_before_min
        out["location"] = it.location
        out["participants"] = it.participants
        out["overdue"] = is_overdue(it)
        out["done"] = is_done(it)
    return out


def device_snapshot(items: list[Item], tz: str) -> dict[str, list[dict[str, Any]]]:
    """The `items` message body: note previews by number, open reminders by due time, then completed ones."""
    now = datetime.now(timezone.utc)
    notes = [
        {"number": it.number, "preview": preview(it.text)}
        for it in sorted(items, key=lambda i: i.number)
        if it.kind == "note"
    ]
    rems = sorted(
        (it for it in items if it.kind == "reminder"), key=lambda i: (is_done(i), i.due_at or now, i.number)
    )
    reminders = [
        {
            "number": it.number,
            "text": it.text,
            "due_local": local_time(it.due_at, tz),
            "end_local": local_time(it.end_at, tz),
            "notify_before": it.notify_before_min,
            "location": it.location,
            "participants": it.participants,
            "overdue": is_overdue(it, now),
            "done": is_done(it),
        }
        for it in rems
    ]
    return {"notes": notes, "reminders": reminders}


# --- assistant tools -------------------------------------------------------------------------

_KIND = {"type": "string", "enum": list(KINDS), "description": "note or reminder"}
_NUMBER = {"type": "integer", "description": "The item's number, e.g. 2 for note #2"}
_TEXT = {
    "type": "string",
    "description": "Note: the full text, any length. Reminder: what to do, at most 80 characters (keep it short).",
}
_SHOW = {
    "type": "boolean",
    "description": "true only when the user explicitly asked to see it. false while you are just looking "
    "something up, e.g. before changing or deleting an item or while asking for confirmation.",
}
_DONE = {
    "type": "boolean",
    "description": "Reminders only: true = mark it completed (done), false = open it again.",
}
_DUE = {
    "type": "string",
    "description": "Reminders only, required: the start, local 'YYYY-MM-DD HH:MM' in the user's time zone. "
    "Resolve 'tomorrow at 9' etc. from the current local date and time. The time of day must come from the "
    "user - never invent one.",
}

_END = {
    "type": "string",
    "description": "Reminders only, optional: when it ends, local 'YYYY-MM-DD HH:MM', for a time range "
    "('from 9:30 to 10', 'meeting 3 to 4 pm'). Leave it out for a single time. In item_update an empty string "
    "removes the end time.",
}

_NOTIFY = {
    "type": "integer",
    "description": "Reminders only, optional: when the user wants to be told in advance ('15 minutes before', "
    "'an hour before'), how many minutes before the start. The watch then alerts at that time and again at the "
    "start. Leave it out otherwise; in item_update 0 removes it.",
}

_LOCATION = {
    "type": "string",
    "description": "Reminders only, optional: where it happens, as the user said it ('Studio Office', 'the "
    "dentist on Main Street'). Only from the user's words; leave it out otherwise. In item_update an empty "
    "string removes it.",
}
_PARTICIPANTS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Reminders only, optional: the people involved, as the user named them (['Ana', 'Mihai']). "
    "Only from the user's words; leave it out otherwise. In item_update an empty list removes them.",
}

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "item_create",
        "description": "Save a new note (text only), or set a new reminder (needs due_local and a short text). "
        "Returns its number.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": _KIND,
                "text": _TEXT,
                "due_local": _DUE,
                "end_local": _END,
                "notify_before_minutes": _NOTIFY,
                "location": _LOCATION,
                "participants": _PARTICIPANTS,
            },
            "required": ["kind", "text"],
        },
    },
    {
        "name": "item_list",
        "description": "List the user's notes or reminders. With show_on_watch=true the list also opens on the "
        "watch screen (when the user wants to see their notes or reminders).",
        "parameters": {
            "type": "object",
            "properties": {"kind": _KIND, "show_on_watch": _SHOW},
            "required": ["kind"],
        },
    },
    {
        "name": "item_show",
        "description": "Read one note or reminder. With show_on_watch=true it also opens full-screen on the watch.",
        "parameters": {
            "type": "object",
            "properties": {"kind": _KIND, "number": _NUMBER, "show_on_watch": _SHOW},
            "required": ["kind", "number"],
        },
    },
    {
        "name": "item_update",
        "description": "Change the text of a note or reminder, a reminder's time, or mark a reminder "
        "completed (done). Pass only what changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": _KIND,
                "number": _NUMBER,
                "text": _TEXT,
                "due_local": _DUE,
                "end_local": _END,
                "notify_before_minutes": _NOTIFY,
                "location": _LOCATION,
                "participants": _PARTICIPANTS,
                "done": _DONE,
            },
            "required": ["kind", "number"],
        },
    },
    {
        "name": "item_delete",
        "description": "Delete a note or reminder by number.",
        "parameters": {
            "type": "object",
            "properties": {"kind": _KIND, "number": _NUMBER},
            "required": ["kind", "number"],
        },
    },
]

TOOLS_RULE = (
    "You keep the user's notes and reminders with the item_* tools. They are separate lists, each numbered "
    "(note #1, reminder #2). Notes are text only; a reminder has a date and time (for a time range also an "
    "end time: due_local = start, end_local = end) and a short text (at most 80 characters: shorten it "
    "yourself). Use the tools whenever the user asks to note, remember, remind, see, "
    "change or delete something. When they want to see their notes or reminders, call item_list with "
    "show_on_watch=true; while you only look things up or ask for confirmation, leave show_on_watch false "
    "(after a create, change or delete the watch opens that list by itself). Never say something was saved, changed or deleted unless the tool reported success. "
    "Say the number of a new item. A reminder always needs a time of day: if the user did not say one (only "
    "a day, or nothing), do not create it yet - ask what time, and keep asking until you have at least a start "
    "time; never pick a time yourself. If they give an end time or a range ('from 9:30 to 10', 'between 3 "
    "and 4'), pass it as end_local; otherwise leave end_local out. If they want to be told in advance "
    "('remind me 15 minutes before', 'an hour before the meeting at 10'), keep due_local at the real start "
    "(10:00) and set notify_before_minutes (15, 60): the watch alerts then and again at the start. The "
    "reminder text is its full short description ('Team sync with Ana at Studio Office'); when it names a "
    "place or people, also pass them as location / participants (only what the user said, never invented), "
    "and when you change the text, update them to match. When the user says a reminder is "
    "done or completed, call item_update with done=true (do not delete it unless they ask)."
)


@dataclass
class ToolOutcome:
    result: str  # JSON text returned to the model
    changed: bool = False
    # What to open on the watch after the reply: {"list": kind} | {"item": {...}}. Set after a change, or for
    # a list / show the user asked to see - never for a lookup during a confirmation.
    open: dict[str, Any] | None = None
    settings_changed: bool = False  # watch settings changed: push them when the turn ends


class AssistantTools:
    """The assistant's tools: watch_settings (every watch) and the item_* tools (watches with an owner)."""

    def definitions(self, account_id: int | None) -> list[dict[str, Any]]:
        return [SETTINGS_TOOL, *(TOOL_DEFS if account_id is not None else [])]

    def rules(self, account_id: int | None) -> list[str]:
        return [SETTINGS_RULE, *([TOOLS_RULE] if account_id is not None else [])]

    def execute(
        self, account_id: int | None, tz: str, name: str, arguments: str, device_id: str = ""
    ) -> ToolOutcome:
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
            if name == SETTINGS_TOOL["name"]:
                result, changed = settings_tool.apply(device_id, args)
                return ToolOutcome(result, settings_changed=changed)
            if account_id is None:
                return ToolOutcome(_err("notes and reminders need a watch linked to an account"))
            return self._run(account_id, tz, name, args)
        except ItemLimitError:
            return ToolOutcome(_err(f"limit reached ({ItemRepo.MAX_PER_KIND}); delete some first"))
        except (ValueError, TypeError, KeyError) as exc:  # ItemTextError is a ValueError
            return ToolOutcome(_err(str(exc) or type(exc).__name__))

    def _run(self, account_id: int, tz: str, name: str, a: dict[str, Any]) -> ToolOutcome:
        kind = a.get("kind")
        if kind not in KINDS:
            return ToolOutcome(_err("kind must be note or reminder"))
        with session_scope() as db:
            repo = ItemRepo(db)
            if name == "item_list":
                rows = [
                    {
                        "number": it.number,
                        "text": it.text,
                        "due_local": local_time(it.due_at, tz),
                        "end_local": local_time(it.end_at, tz),
                        "notify_before_minutes": it.notify_before_min,
                        "location": it.location,
                        "participants": it.participants,
                        "overdue": is_overdue(it),
                        "done": is_done(it),
                    }
                    if kind == "reminder"
                    else {"number": it.number, "preview": preview(it.text)}
                    for it in repo.list(account_id, kind)
                ]
                show = {"list": kind} if a.get("show_on_watch") is True else None
                return ToolOutcome(json.dumps({"ok": True, "items": rows}, ensure_ascii=False), open=show)
            if name == "item_create":
                due = None
                if kind == "reminder":
                    if not a.get("due_local"):
                        return ToolOutcome(_err(
                            "a reminder needs due_local with a time of day. Ask the user what time "
                            "(at least the start time); do not create it without one."
                        ))
                    due = parse_local_time(a["due_local"], tz, "due_local")
                end = parse_local_time(a["end_local"], tz, "end_local") if kind == "reminder" and a.get("end_local") else None
                notify = (
                    int(a["notify_before_minutes"]) if kind == "reminder" and a.get("notify_before_minutes") else None
                )
                it = repo.create(
                    account_id,
                    kind,
                    str(a.get("text") or ""),
                    due,
                    end,
                    notify,
                    location=str(a.get("location") or "") if kind == "reminder" else None,
                    participants=_names(a.get("participants")) if kind == "reminder" else None,
                )
                return ToolOutcome(_ok(it, tz), changed=True, open={"list": kind})
            number = int(a.get("number"))
            it = repo.get(account_id, kind, number)
            if it is None:
                return ToolOutcome(_err(f"{kind} #{number} does not exist"))
            if name == "item_show":
                show = {"item": device_full(it, tz)} if a.get("show_on_watch") is True else None
                return ToolOutcome(_ok(it, tz, full=True), open=show)
            if name == "item_update":
                due = parse_local_time(a["due_local"], tz, "due_local") if a.get("due_local") and kind == "reminder" else None
                done = a.get("done") if isinstance(a.get("done"), bool) else None
                extra: dict[str, Any] = {}
                if kind == "reminder" and "end_local" in a:  # "" or null removes the end time
                    extra["end_at"] = parse_local_time(a["end_local"], tz, "end_local") if a["end_local"] else None
                if kind == "reminder" and "notify_before_minutes" in a:  # 0 or null removes it
                    extra["notify_before_min"] = int(a["notify_before_minutes"] or 0) or None
                if kind == "reminder" and "location" in a:  # "" or null removes it
                    extra["location"] = str(a["location"] or "")
                if kind == "reminder" and "participants" in a:  # [] or null removes them
                    extra["participants"] = _names(a["participants"])
                it = repo.update(
                    it, text=str(a["text"]) if a.get("text") else None, due_at=due, done=done, **extra
                )
                return ToolOutcome(_ok(it, tz), changed=True, open={"list": kind})
            if name == "item_delete":
                repo.delete(it)
                return ToolOutcome(
                    json.dumps({"ok": True, "deleted": f"{kind} #{number}"}), changed=True, open={"list": kind}
                )
        return ToolOutcome(_err(f"unknown tool {name}"))


def _ok(it: Item, tz: str, full: bool = False) -> str:
    body: dict[str, Any] = {"ok": True, "kind": it.kind, "number": it.number}
    if it.kind == "reminder":
        body.update(
            text=it.text,
            due_local=local_time(it.due_at, tz),
            end_local=local_time(it.end_at, tz),
            notify_before_minutes=it.notify_before_min,
            location=it.location,
            participants=it.participants,
            overdue=is_overdue(it),
            done=is_done(it),
        )
    else:
        body["text" if full else "preview"] = it.text if full else preview(it.text)
    return json.dumps(body, ensure_ascii=False)


def _names(value: Any) -> str:
    """Participants from the model (a list, or one comma-separated string) -> "Ana, Mihai"."""
    parts = value if isinstance(value, list) else str(value or "").split(",")
    return ", ".join(p for p in (" ".join(str(x).split()) for x in parts) if p)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})
