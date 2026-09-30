"""Notes and reminders: views for the watch and the web app, and the assistant's function tools.

Notes are text only (up to 10000 characters). Reminders have a due time and a short text (up to 80
characters); once the time has passed they are "overdue" until completed, deleted or rescheduled. A
completed reminder (`done`) no longer fires or counts as overdue. Items belong to the account; numbers are per (account, kind) and a new item takes the
lowest free number (see ItemRepo). The watch gets a light snapshot (`items`); a note's full text is
sent when it is opened (`item_show`), a due reminder with `reminder_fire`.
"""

from __future__ import annotations

import json
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
    "description": "Reminders only: local 'YYYY-MM-DD HH:MM' in the user's time zone. Resolve 'tomorrow at 9' etc. "
    "from the current local date and time.",
}

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "item_create",
        "description": "Save a new note (text only), or set a new reminder (needs due_local and a short text). "
        "Returns its number.",
        "parameters": {
            "type": "object",
            "properties": {"kind": _KIND, "text": _TEXT, "due_local": _DUE},
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
            "properties": {"kind": _KIND, "number": _NUMBER, "text": _TEXT, "due_local": _DUE, "done": _DONE},
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
    "(note #1, reminder #2). Notes are text only; a reminder has a date and time and a short text (at most 80 "
    "characters: shorten it yourself). Use the tools whenever the user asks to note, remember, remind, see, "
    "change or delete something. When they want to see their notes or reminders, call item_list with "
    "show_on_watch=true; while you only look things up or ask for confirmation, leave show_on_watch false "
    "(after a create, change or delete the watch opens that list by itself). Never say something was saved, changed or deleted unless the tool reported success. "
    "Say the number of a new item. If a reminder's time is unclear, ask. When the user says a reminder is "
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
                        return ToolOutcome(_err("a reminder needs due_local"))
                    due = parse_local(str(a["due_local"]), tz)
                it = repo.create(account_id, kind, str(a.get("text") or ""), due)
                return ToolOutcome(_ok(it, tz), changed=True, open={"list": kind})
            number = int(a.get("number"))
            it = repo.get(account_id, kind, number)
            if it is None:
                return ToolOutcome(_err(f"{kind} #{number} does not exist"))
            if name == "item_show":
                show = {"item": device_full(it, tz)} if a.get("show_on_watch") is True else None
                return ToolOutcome(_ok(it, tz, full=True), open=show)
            if name == "item_update":
                due = parse_local(str(a["due_local"]), tz) if a.get("due_local") and kind == "reminder" else None
                done = a.get("done") if isinstance(a.get("done"), bool) else None
                it = repo.update(it, text=str(a["text"]) if a.get("text") else None, due_at=due, done=done)
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
        body.update(text=it.text, due_local=local_time(it.due_at, tz), overdue=is_overdue(it), done=is_done(it))
    else:
        body["text" if full else "preview"] = it.text if full else preview(it.text)
    return json.dumps(body, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})
