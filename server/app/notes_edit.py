"""Note edit mode: one note, edited line by line by voice from the watch's note screen.

The watch keeps the microphone open; every sentence is its own short turn (`listen_start` with
`mode: "note"`). The assistant here does nothing but edit that note: a minimal prompt with the note's
numbered lines, the low-cost model, no history, no persona, no other tools, no spoken reply. It answers
with line operations (`note_edit`), never the whole text, so long notes stay cheap. Silence costs
nothing: only speech reaches the (billed) STT, and a turn that hears nothing is not stored.

A note's text is plain lines. The first line is the note's title (shown large, and as the note's name in
lists); the lines under it are numbered 1, 2, 3 on the watch, and those are the numbers the user says. The
numbers are only drawn by the watch; list markers a user may have typed ("- ", "1. ") are dropped here.
"""

from __future__ import annotations

import json
import re
from typing import Any

MARKER = re.compile(r"^\s*(?:[-–—•*]|\d{1,3}[.)])\s+")
MAX_OPS = 10

NOTE_EDIT_TOOL: dict[str, Any] = {
    "name": "note_edit",
    "description": "Apply the user's sentence to the note: add, insert, change, delete or move lines, or undo "
    "the previous change.",
    "parameters": {
        "type": "object",
        "properties": {
            "ops": {
                "type": "array",
                "description": "Operations in order. Line numbers refer to the note as it is before this call.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": ["append", "insert", "replace", "delete", "clear", "move", "title", "undo"],
                        },
                        "line": {
                            "type": "integer",
                            "description": "numbered line under the title, from 1 (insert: the new line's position)",
                        },
                        "match": {
                            "type": "string",
                            "description": "replace / delete / move: the user's words for the line when they name it "
                            "by its content ('the milk' -> 'milk') instead of a number",
                        },
                        "to": {"type": "integer", "description": "move: the line's new position (1-based)"},
                        "text": {"type": "string", "description": "append / insert / replace / title: the text"},
                    },
                    "required": ["op"],
                },
            }
        },
        "required": ["ops"],
    },
}

IGNORE = "IGNORE"  # the model's whole reply for speech that is not meant for the item: nothing changes

NOTE_SYSTEM = (
    "You edit ONE note on a smartwatch by voice. The note has a title and, under it, numbered lines (1, 2, "
    "3 - line numbers in your operations are these). Each user message is one spoken sentence. Always "
    "answer with a single note_edit call:\n"
    "- If the note is empty, the first dictated sentence becomes its title: append it, shortened to a few "
    "words if it is long. 'Call it ...' / 'rename the note to ...' -> op title.\n"
    "- Dictated content (anything that is not an editing command) -> append it as a new line, cleaned up: "
    "fix the transcription's punctuation and capitalization, keep the user's words and language, no "
    "numbering or bullet in the text.\n"
    "- Commands like 'delete 3', 'change 2 to …', 'replace milk with bread', 'move 4 to the top', 'put this "
    "after 1', 'undo' -> the matching operations. When the user names a line by its content ('delete the "
    "milk'), pass match with their words instead of guessing a line number; use line only when they say a number "
    "(or answer your question about which line).\n"
    "- The microphone stays open, so it also hears speech not meant for the note: someone talking to the user, "
    "a TV or radio, a lone filler word or exclamation ('ok', 'hmm', 'wow'). For that, do not call the tool: "
    f"reply only {IGNORE}.\n"
    "- If the user talks about a DIFFERENT note or reminder than this one, do not call the tool: reply only "
    "OTHER.\n"
    "- Never answer questions or chat; you only edit this note. If a command is unclear (e.g. the line "
    "does not exist), do not call the tool: reply with a very short question in the user's language, at "
    "most 60 characters."
)


def note_lines(text: str) -> list[str]:
    """A note's text -> its non-empty lines without list markers."""
    out = []
    for raw in (text or "").splitlines():
        line = MARKER.sub("", raw).strip()
        if line:
            out.append(line)
    return out


def numbered(lines: list[str]) -> str:
    """The note as the model sees it: the title, then the numbered lines (the watch's numbers)."""
    if not lines:
        return "(empty note)"
    body = "\n".join(f"{i}. {line}" for i, line in enumerate(lines[1:], 1))
    return f"Title: {lines[0]}\n{body or '(no lines yet)'}"


def apply_note_ops(lines: list[str], ops: Any, undo_lines: list[str] | None) -> tuple[list[str], int | None]:
    """`note_edit` operations numbered like the watch (lines under the title) -> apply_ops.

    Returns (new lines, highlight): 0 = the title, n = numbered line n, None = nothing to highlight.
    """
    if not isinstance(ops, list):
        raise ValueError("ops must be a list")
    shifted = []
    for o in ops:
        if not isinstance(o, dict):
            raise ValueError("bad operation")
        o = dict(o)
        if o.get("op") == "title":
            o = {"op": "replace", "line": 1, "text": o.get("text")} if lines else {"op": "append", "text": o.get("text")}
        else:
            for key in ("line", "to"):
                if isinstance(o.get(key), int):
                    o[key] += 1  # numbered line n is line n + 1 of the text
            if o.get("op") in ("replace", "delete", "move") and isinstance(o.get("line"), int) and o["line"] < 2:
                raise ValueError("the title is not a numbered line")
        shifted.append(o)
    out, highlight = apply_ops(lines, shifted, undo_lines)
    return out, None if highlight is None else highlight - 1


def apply_ops(lines: list[str], ops: Any, undo_lines: list[str] | None) -> tuple[list[str], int | None]:
    """Apply `note_edit` operations. Returns (new lines, 1-based line to highlight or None).

    Line numbers in the operations refer to the note before the call; each is mapped to the line it
    named, so "delete 2, delete 3" removes the two lines the user saw. Raises ValueError on a bad op.
    """
    if not isinstance(ops, list) or not ops:
        raise ValueError("ops must be a non-empty list")
    if len(ops) > MAX_OPS:
        raise ValueError("too many operations")
    if any(isinstance(o, dict) and o.get("op") == "undo" for o in ops):
        if undo_lines is None:
            raise ValueError("nothing to undo")
        return list(undo_lines), None
    # Work on (original index, text) pairs so original line numbers stay meaningful.
    rows: list[tuple[int | None, str]] = list(enumerate(lines))
    changed: int | None = None  # original index or id() of a new row

    def pos_of(n: Any) -> int:
        if not isinstance(n, int) or not 1 <= n <= len(lines):
            raise ValueError(f"line {n} does not exist")
        for i, (orig, _t) in enumerate(rows):
            if orig == n - 1:
                return i
        raise ValueError(f"line {n} was already removed")

    def text_of(o: dict[str, Any]) -> str:
        t = " ".join(str(o.get("text") or "").split())
        t = MARKER.sub("", t)
        if not t:
            raise ValueError("text is empty")
        return t

    marker = -1  # new rows get negative ids
    for o in ops:
        if not isinstance(o, dict):
            raise ValueError("bad operation")
        kind = o.get("op")
        if kind == "append":
            rows.append((marker, text_of(o)))
            changed, marker = marker, marker - 1
        elif kind == "insert":
            at = o.get("line")
            if not isinstance(at, int):
                raise ValueError("insert needs line")
            i = pos_of(at) if 1 <= at <= len(lines) else len(rows)
            rows.insert(i, (marker, text_of(o)))
            changed, marker = marker, marker - 1
        elif kind == "replace":
            i = pos_of(o.get("line"))
            rows[i] = (rows[i][0], text_of(o))
            changed = rows[i][0]
        elif kind == "delete":
            del rows[pos_of(o.get("line"))]
        elif kind == "move":
            i = pos_of(o.get("line"))
            to = o.get("to")
            if not isinstance(to, int):
                raise ValueError("move needs to")
            row = rows.pop(i)
            rows.insert(max(0, min(len(rows), to - 1)), row)
            changed = row[0]
        else:
            raise ValueError(f"unknown op {kind!r}")
    out = [t for _o, t in rows]
    highlight = next((i + 1 for i, (o, _t) in enumerate(rows) if o == changed), None) if changed is not None else None
    return out, highlight


def parse_ops(arguments: str) -> Any:
    try:
        return (json.loads(arguments or "{}") or {}).get("ops")
    except (ValueError, AttributeError):
        raise ValueError("arguments must be JSON with ops") from None


# One-step undo per (account, note number): the lines before the last change made in note mode.
# One-step undo per (account, note uid): the lines before the last change made in note mode and the version
# that change produced - undo only applies while the note is still at that version.
_UNDO: dict[tuple[int, str], tuple[list[str], int]] = {}
_UNDO_MAX = 500


def undo_get(account_id: int, uid: str, version: int) -> list[str] | None:
    got = _UNDO.get((account_id, uid))
    return list(got[0]) if got is not None and got[1] == version else None


def undo_set(account_id: int, uid: str, lines: list[str], version: int) -> None:
    if len(_UNDO) >= _UNDO_MAX:
        _UNDO.pop(next(iter(_UNDO)))
    _UNDO[(account_id, uid)] = (list(lines), version)
