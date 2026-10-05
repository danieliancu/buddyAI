"""The assistant's note / reminder tools for voice (chat mode), with server-side safety:

- items are found by what the user says (app.item_search), referred to by refs the server issued
  (app.voice_context) - the model can never name an arbitrary id, an item of another account, or pick one
  of several candidates in the turn they were shown (the user chooses first);
- every change is written with a version check (nothing made meanwhile is overwritten) and recorded in
  `voice_operations` in the same transaction (a technical retry returns the stored result);
- nothing is ever deleted here: a deletion (an item, note lines, a reminder's details) only *prepares* a
  PendingConfirmation; it runs after an explicit yes in a later turn (pipeline confirmation gate, which
  calls execute_confirmation).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app import edit_texts, notes_edit
from app.db.models import Item, VoiceOperation
from app.db.repositories import ItemConflictError, ItemRepo, VoiceOpRepo
from app.db.session import session_scope
from app.item_search import PREFIX, ItemQuery, SearchResult, best_match, normalize, search, tokens
from app.tool_outcome import ToolOutcome
from app.items import (
    _names,
    device_full,
    is_done,
    is_overdue,
    keep_date_with_time,
    keep_time_on_date,
    local_time,
    on_watch,
    parse_local_time,
    preview,
)
from app.voice_context import (
    CLARIFY_TTL_S,
    CONFIRM_TTL_S,
    STORE,
    PendingClarification,
    PendingConfirmation,
    RefError,
    VoiceContext,
    now as mono_now,
    op_key,
)

log = logging.getLogger(__name__)
MAX_BULK_DELETE = 20
REMOVABLE = ("location", "participants", "end", "notify")


@dataclass
class ToolCallCtx:
    """Who is asking: always from the authenticated connection, never from the model."""

    account_id: int
    device_id: str
    session_id: str
    turn_id: int
    tz: str
    mode: str = "chat"
    language: str = "en"
    user_text: str = ""  # the user's sentence (to tell whether a line or a person was named ambiguously)


def _json(body: dict[str, Any]) -> str:
    return json.dumps(body, ensure_ascii=False)


def _err(msg: str, **extra: Any) -> ToolOutcome:
    return ToolOutcome(_json({"ok": False, "error": msg, **extra}))


# --- descriptions ------------------------------------------------------------------------------------


def summary(it: Item, tz: str) -> str:
    """One line about an item, for the model (no ids)."""
    if it.kind == "note":
        lines = notes_edit.note_lines(it.text)
        return f"note «{preview(it.text) or '(empty)'}» ({max(0, len(lines) - 1)} lines, #{it.number} on the watch)"
    zone = ZoneInfo(tz)
    when = ""
    if it.due_at:
        start = it.due_at.astimezone(zone)
        when = f"{start:%a %Y-%m-%d %H:%M}"
        if it.end_at:
            when += f"-{it.end_at.astimezone(zone):%H:%M}"
    extra = "".join(
        [f", at {it.location}" if it.location else "", f", with {it.participants}" if it.participants else "",
         ", completed" if is_done(it) else ""]
    )
    return f"reminder «{it.text}», {when}{extra} (#{it.number} on the watch)"


def item_view(it: Item, tz: str, ref: str | None = None, full: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": it.kind, "number": it.number}
    if ref:
        body["ref"] = ref
    if it.kind == "note":
        lines = notes_edit.note_lines(it.text)
        if full:
            body["title"] = lines[0] if lines else ""
            body["lines"] = {str(i): ln for i, ln in enumerate(lines[1:], start=1)}
        else:
            body["title"] = preview(it.text)
            body["line_count"] = max(0, len(lines) - 1)
    else:
        body.update(
            text=it.text,
            due_local=local_time(it.due_at, tz),
            end_local=local_time(it.end_at, tz),
            weekday=it.due_at.astimezone(ZoneInfo(tz)).strftime("%A") if it.due_at else None,
            notify_before_minutes=it.notify_before_min,
            location=it.location,
            participants=it.participants,
            done=is_done(it),
            overdue=is_overdue(it),
        )
    return body


# --- the executor --------------------------------------------------------------------------------------


class VoiceItemTools:
    """Runs one item tool call for an authenticated voice turn."""

    def __init__(self, call: ToolCallCtx) -> None:
        self.call = call
        self.ctx: VoiceContext = STORE.get(call.account_id, call.device_id, call.session_id)

    # entry point
    def run(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        handler = {
            "item_find": self.find,
            "item_list": self.list,
            "item_show": self.show,
            "item_create": self.create,
            "item_update": self.update,
            "item_note_edit": self.note_edit,
            "item_duplicate": self.duplicate,
            "item_delete": self.delete,
            "item_choose": self.choose,
        }.get(name)
        if handler is None:
            return _err(f"unknown tool {name}")
        return handler(_clean(args))

    # --- lookups --------------------------------------------------------------------------------

    def _items(self, db, kind: str | None = None) -> list[Item]:
        return ItemRepo(db).list(self.call.account_id, kind if kind in ("note", "reminder") else None)

    def _search(self, db, query: dict[str, Any], purpose: str) -> tuple[ItemQuery, SearchResult]:
        q = ItemQuery.from_args({**query, "purpose": purpose})
        return q, search(self._items(db), q, self.call.tz)

    def find(self, a: dict[str, Any]) -> ToolOutcome:
        with session_scope() as db:
            q, res = self._search(db, a, "read")
            refs = STORE.issue_refs(
                self.ctx, [(c.item, summary(c.item, self.call.tz)) for c in res.candidates], self.call.turn_id,
                selectable_now=res.status == "found",
            )
            cands = []
            for ref, c in zip(refs, res.candidates):
                view = item_view(c.item, self.call.tz, ref, full=True)
                view["match"] = c.reasons
                view["exact"] = c.exact
                if c.line is not None:
                    view["found_in_line"] = c.line
                cands.append(view)
            body = {
                "ok": True, "status": res.status, "total": res.total, "returned": len(cands),
                "truncated": res.truncated, "candidates": cands, "differences": res.differences,
                "hint": _hint(res.status),
            }
            out = ToolOutcome(_json(body), awaits_answer=res.status in ("ambiguous", "insufficient"))
            if a.get("show_on_watch") is True and res.status == "found":
                it = res.candidates[0].item
                out.open, out.open_uid = {"item": device_full(it, self.call.tz)}, it.uid
                STORE.set_open(self.ctx, it, summary(it, self.call.tz), self.call.turn_id)
            return out

    def list(self, a: dict[str, Any]) -> ToolOutcome:
        kind = a.get("kind")
        if kind not in ("note", "reminder"):
            return _err("kind must be note or reminder")
        with session_scope() as db:
            items = [it for it in self._items(db, kind) if on_watch(it, self.call.tz)]
            # a list is for reading: picking one of them for a change waits for the user's choice
            refs = STORE.issue_refs(self.ctx, [(it, summary(it, self.call.tz)) for it in items], self.call.turn_id,
                                    selectable_now=False)
            rows = [item_view(it, self.call.tz, ref) for ref, it in zip(refs, items)]
        show = {"list": kind} if a.get("show_on_watch") is True else None
        if show:
            STORE.clear_open(self.ctx)
        return ToolOutcome(_json({"ok": True, "items": rows}), open=show)

    def show(self, a: dict[str, Any]) -> ToolOutcome:
        with session_scope() as db:
            got = self._target(db, a, "read", op="show")
            if isinstance(got, ToolOutcome):
                return got
            it, ref = got
            out = ToolOutcome(_json({"ok": True, **item_view(it, self.call.tz, ref, full=True)}))
            if a.get("show_on_watch") is True:
                out.open, out.open_uid = {"item": device_full(it, self.call.tz)}, it.uid
                STORE.set_open(self.ctx, it, summary(it, self.call.tz), self.call.turn_id)
            return out

    def _target(self, db, a: dict[str, Any], purpose: str, op: str) -> tuple[Item, str] | ToolOutcome:
        """The item a call is about: a ref the server issued, or a query that must find exactly one."""
        target = a.get("target") if isinstance(a.get("target"), dict) else {}
        ref = target.get("ref") or a.get("ref")
        repo = ItemRepo(db)
        if ref:
            try:
                r = STORE.resolve(self.ctx, str(ref), self.call.turn_id, for_change=purpose != "read")
            except RefError as exc:
                return _err(str(exc))
            it = repo.get_by_uid(self.call.account_id, r.uid)
            if it is None:
                return _err("that item no longer exists (deleted meanwhile); tell the user, do not use another one")
            if purpose != "read" and it.version != r.version:
                STORE.refresh_version(self.ctx, it.uid, it.version)
                return _err(
                    "the item changed since it was shown (edited elsewhere): tell the user its current state and "
                    "ask again before changing it", current=item_view(it, self.call.tz, str(ref), full=True)
                )
            return it, str(ref)
        query = target.get("query") if isinstance(target.get("query"), dict) else None
        if not query:
            if self.ctx.open_item is not None:
                return self._target(db, {"target": {"ref": "open"}}, purpose, op)
            return _err("say which item: pass target.query with what the user said (words, person, place, day)")
        q, res = self._search(db, query, purpose)
        if res.status == "found":
            it = res.candidates[0].item
            [name] = STORE.issue_refs(self.ctx, [(it, summary(it, self.call.tz))], self.call.turn_id, selectable_now=True)
            return it, name
        refs = STORE.issue_refs(self.ctx, [(c.item, summary(c.item, self.call.tz)) for c in res.candidates],
                                self.call.turn_id, selectable_now=False)
        if res.status == "ambiguous":
            STORE.put_clarification(self.ctx, PendingClarification(
                op, {k: v for k, v in a.items() if k != "target"}, refs, "", self.call.turn_id,
                mono_now() + CLARIFY_TTL_S, self.call.mode,
            ))
            return ToolOutcome(_json({
                "ok": False, "status": "ambiguous", "total": res.total, "truncated": res.truncated,
                "candidates": [item_view(c.item, self.call.tz, r, full=True) | {"match": c.reasons}
                               for r, c in zip(refs, res.candidates)],
                "differences": res.differences,
                "hint": "Nothing was changed. Ask the user one short question using the differences (day, time, "
                "place, people, text) - never option numbers. Their answer goes to item_choose.",
            }), awaits_answer=True)
        if res.status == "insufficient":
            return ToolOutcome(_json({"ok": False, "status": "insufficient",
                                      "hint": "Nothing was changed. Ask what the item is about."}), awaits_answer=True)
        return ToolOutcome(_json({"ok": False, "status": "not_found",
                                  "hint": "Nothing was changed or created. Say you could not find it and ask for a "
                                  "detail (a word from it, a person, a day)."}), awaits_answer=True)

    # --- durable, idempotent writes --------------------------------------------------------------

    def _op(self, tool: str, args: dict[str, Any]) -> tuple[str, VoiceOperation]:
        key = op_key(self.call.account_id, self.call.device_id, self.call.session_id, self.call.turn_id, tool, args)
        return key, VoiceOperation(
            op_key=key, account_id=self.call.account_id, device_id=self.call.device_id,
            session_id=self.call.session_id, turn_id=self.call.turn_id, tool=tool[:32],
        )

    def _done_before(self, db, key: str) -> ToolOutcome | None:
        prev = VoiceOpRepo(db).get(key)
        if prev is None:
            return None
        stored = json.loads(prev.result or "{}")
        out = ToolOutcome(_json({**stored.get("result", {}), "note": "already done (repeated call): not done twice"}))
        out.open = stored.get("open")
        out.open_uid = stored.get("open_uid")
        return out

    @staticmethod
    def _record(op: VoiceOperation, out: ToolOutcome, summary_text: str) -> None:
        op.summary = summary_text[:300]
        op.result = _json({"result": json.loads(out.result), "open": out.open, "open_uid": out.open_uid})

    def _opened(self, it: Item, extra: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
        view = device_full(it, self.call.tz)
        if extra:
            view.update(extra)
        return {"item": view}, it.uid

    # --- create -----------------------------------------------------------------------------------

    def create(self, a: dict[str, Any]) -> ToolOutcome:
        kind = a.get("kind")
        if kind not in ("note", "reminder"):
            return _err("kind must be note or reminder")
        tz = self.call.tz
        due = end = None
        notify = None
        if kind == "reminder":
            if not a.get("due_local"):
                return _err("a reminder needs due_local with a time of day. Ask the user what time; do not create it without one.")
            due = parse_local_time(a["due_local"], tz, "due_local")
            end = parse_local_time(a["end_local"], tz, "end_local") if a.get("end_local") else None
            notify = int(a["notify_before_minutes"]) if a.get("notify_before_minutes") else None
        key, op = self._op("item_create", a)
        with session_scope() as db:
            if (prev := self._done_before(db, key)) is not None:
                return prev
            try:
                it = ItemRepo(db).create(
                    self.call.account_id, kind, str(a.get("text") or ""), due, end, notify,
                    location=str(a.get("location") or "") if kind == "reminder" else None,
                    participants=_names(a.get("participants")) if kind == "reminder" else None,
                    op=self._prepare(op, "created", a),
                )
            except IntegrityError:
                db.rollback()
                if (prev := self._done_before(db, key)) is not None:
                    return prev
                raise
            return self._finish(db, op, it, "created")

    def _prepare(self, op: VoiceOperation, what: str, a: dict[str, Any]) -> VoiceOperation:
        op.summary = f"{what}: {str(a.get('text') or a)[:200]}"
        op.result = "{}"
        return op

    def _finish(self, db, op: VoiceOperation, it: Item, what: str, extra_open: dict[str, Any] | None = None,
                extra_body: dict[str, Any] | None = None) -> ToolOutcome:
        [ref] = STORE.issue_refs(self.ctx, [(it, summary(it, self.call.tz))], self.call.turn_id, selectable_now=True)
        STORE.set_open(self.ctx, it, summary(it, self.call.tz), self.call.turn_id)
        body = {"ok": True, what: True, **item_view(it, self.call.tz, ref, full=True), **(extra_body or {})}
        out = ToolOutcome(_json(body), changed=True)
        out.open, out.open_uid = self._opened(it, extra_open)
        # store the result on the operation record (already committed with the change)
        row = VoiceOpRepo(db).get(op.op_key)
        if row is not None:
            self._record(row, out, f"{what} {summary(it, self.call.tz)}")
            db.add(row)
            db.commit()
        return out

    # --- update (reminders) --------------------------------------------------------------------------

    def update(self, a: dict[str, Any]) -> ToolOutcome:
        a = self._merge_clarification("update", a)
        changes = a.get("changes") if isinstance(a.get("changes"), dict) else {}
        with session_scope() as db:
            got = self._target(db, a, "change", op="update")
            if isinstance(got, ToolOutcome):
                return got
            it, ref = got
            if it.kind != "reminder":
                return _err("item_update is for reminders; for a note use item_note_edit (lines are kept)")
            try:
                kw, removed = reminder_changes(it, changes, self.call.tz)
            except AmbiguousPerson as exc:
                return self._continue_on_screen(it, edit_texts.which_of(self.call.language, exc.names[:3]),
                                                {"status": "ambiguous_person", "said": exc.said, "names": exc.names})
            if not kw:
                return _err("no change given")
            if removed:
                return self._ask_confirmation(
                    "reminder_change", [(it.uid, it.version)], {"changes": _serial(kw),
                                                                "opens": reminder_add_or_change(it, kw)},
                    f"remove {', '.join(removed)} from {summary(it, self.call.tz)}", it.kind,
                )
            key, op = self._op("item_update", {"uid": it.uid, "changes": changes})
            if (prev := self._done_before(db, key)) is not None:
                return prev
            try:
                it2 = ItemRepo(db).update(it, **kw, expected_version=it.version, op=self._prepare(op, "changed", changes))
            except ItemConflictError:
                return _err("the item changed meanwhile (edited elsewhere): nothing was changed; tell the user and ask again")
            return self._finish(db, op, it2, "changed")

    def _merge_clarification(self, op_name: str, a: dict[str, Any]) -> dict[str, Any]:
        """The user chose one of the candidates: keep the changes asked for before the question."""
        pc = STORE.peek_clarification(self.ctx)
        target = a.get("target") if isinstance(a.get("target"), dict) else {}
        ref = target.get("ref") or a.get("ref")
        if pc is None or pc.op != op_name or not ref or ref not in pc.refs:
            return a
        STORE.take_clarification(self.ctx)
        merged = {**pc.args}
        for k, v in a.items():
            if k in ("changes",) and isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            elif k == "ops" and not v:
                continue
            else:
                merged[k] = v
        return merged

    def choose(self, a: dict[str, Any]) -> ToolOutcome:
        pc = STORE.peek_clarification(self.ctx)
        ref = str(a.get("ref") or "")
        if pc is None:
            return _err("there is no pending choice; call the operation directly")
        if ref not in pc.refs:
            return _err(f"choose one of {pc.refs} (the candidates shown to the user)")
        extra = a.get("extra_changes") if isinstance(a.get("extra_changes"), dict) else {}
        args: dict[str, Any] = {"target": {"ref": ref}}
        if extra:
            args["changes"] = extra
        handler = {"update": self.update, "note_edit": self.note_edit, "duplicate": self.duplicate,
                   "delete": self.delete, "show": self.show}[pc.op]
        return handler(args)

    # --- note edit ----------------------------------------------------------------------------------

    def note_edit(self, a: dict[str, Any]) -> ToolOutcome:
        a = self._merge_clarification("note_edit", a)
        with session_scope() as db:
            got = self._target(db, a, "change", op="note_edit")
            if isinstance(got, ToolOutcome):
                return got
            it, ref = got
            if it.kind != "note":
                return _err("item_note_edit is for notes; for a reminder use item_update")
            lines = notes_edit.note_lines(it.text)
            ops = a.get("ops")
            try:
                ops = resolve_line_matches(lines, ops)
                check_line_ambiguity(lines, ops, self.call.user_text)
            except AmbiguousLine as exc:
                lang = self.call.language
                options = [f"{edit_texts.text(lang, 'line', n=str(x['line']))} \u00ab{x['text'][:24]}\u00bb" for x in exc.lines[:3]]
                return self._continue_on_screen(it, edit_texts.which_of(lang, options),
                                                {"status": "ambiguous_line", "lines": exc.lines})
            except ValueError as exc:
                return _err(str(exc))
            destructive = note_ops_destructive(ops)
            try:
                new_lines, highlight = notes_edit.apply_note_ops(lines, ops, None)
            except ValueError as exc:
                return _err(str(exc))
            if destructive:
                return self._ask_confirmation(
                    "note_ops", [(it.uid, it.version)], {"text": "\n".join(new_lines), "highlight": highlight,
                                                         "opens": note_ops_add_or_change(ops)},
                    f"delete {describe_deleted_lines(lines, ops)} from {summary(it, self.call.tz)}", it.kind,
                )
            key, op = self._op("item_note_edit", {"uid": it.uid, "ops": ops})
            if (prev := self._done_before(db, key)) is not None:
                return prev
            try:
                it2 = ItemRepo(db).set_note_text(it, "\n".join(new_lines), expected_version=it.version,
                                                op=self._prepare(op, "note changed", {"ops": ops}))
            except ItemConflictError:
                return _err("the note changed meanwhile (edited elsewhere): nothing was changed; ask the user again")
            return self._finish(db, op, it2, "changed", extra_open={"changed_line": highlight})

    # --- duplicate ----------------------------------------------------------------------------------

    def duplicate(self, a: dict[str, Any]) -> ToolOutcome:
        a = self._merge_clarification("duplicate", a)
        changes = a.get("changes") if isinstance(a.get("changes"), dict) else {}
        with session_scope() as db:
            got = self._target(db, a, "duplicate", op="duplicate")
            if isinstance(got, ToolOutcome):
                return got
            src, ref = got
            tz = self.call.tz
            overrides: dict[str, Any] = {}
            extra_body: dict[str, Any] = {}
            if src.kind == "reminder":
                try:
                    due = copy_due(src, changes, tz)
                except AskUser as exc:
                    return ToolOutcome(_json({"ok": False, "status": "ask", "hint": str(exc)}), awaits_answer=True)
                if due <= datetime.now(timezone.utc):
                    return ToolOutcome(_json({"ok": False, "status": "ask", "hint": "That date and time is in the "
                                              "past. Nothing was copied: ask which day."}), awaits_answer=True)
                overrides["due_at"] = due
                overrides["end_at"] = src.end_at + (due - src.due_at) if src.end_at and src.due_at else None
                if not (changes.get("due_local") or changes.get("time_local")):
                    extra_body["kept_time"] = due.astimezone(ZoneInfo(tz)).strftime("%H:%M")
                if changes.get("text"):
                    overrides["text"] = str(changes["text"])
                if "location" in changes and changes["location"]:
                    overrides["location"] = str(changes["location"])
                if changes.get("add_participants"):
                    overrides["participants"] = _names([*(_split(src.participants)), *_split_any(changes["add_participants"])])
            else:
                ops = changes.get("ops") or []
                if ops:
                    if note_ops_destructive(ops):
                        return _err("a copy can only get lines added or changed; nothing was copied")
                    try:
                        lines, _ = notes_edit.apply_note_ops(notes_edit.note_lines(src.text), resolve_line_matches(notes_edit.note_lines(src.text), ops), None)
                    except (ValueError, AmbiguousLine) as exc:
                        return _err(str(exc))
                    overrides["text"] = "\n".join(lines)
            key, op = self._op("item_duplicate", {"uid": src.uid, "changes": changes})
            if (prev := self._done_before(db, key)) is not None:
                return prev
            try:
                copy = ItemRepo(db).duplicate(src, op=self._prepare(op, "copied", changes), **overrides)
            except IntegrityError:
                db.rollback()
                if (prev := self._done_before(db, key)) is not None:
                    return prev
                raise
            return self._finish(db, op, copy, "copied", extra_body=extra_body)

    # --- delete (prepares only) -----------------------------------------------------------------------

    def delete(self, a: dict[str, Any]) -> ToolOutcome:
        a = self._merge_clarification("delete", a)
        targets = a.get("targets") if isinstance(a.get("targets"), list) else []
        with session_scope() as db:
            repo = ItemRepo(db)
            items: list[Item] = []
            if targets:
                for ref in targets[:MAX_BULK_DELETE + 1]:
                    try:
                        r = STORE.resolve(self.ctx, str(ref), self.call.turn_id, for_change=True)
                    except RefError as exc:
                        return _err(str(exc))
                    it = repo.get_by_uid(self.call.account_id, r.uid)
                    if it is None:
                        return _err("one of them no longer exists; nothing was deleted")
                    items.append(it)
            elif a.get("all_matching") is True and isinstance(a.get("query"), dict):
                q, res = self._search(db, a["query"], "delete")
                if res.status == "insufficient":
                    return _err("say which items to delete")
                full = search(self._items(db), ItemQuery(**{**q.__dict__, "limit": MAX_BULK_DELETE + 1}), self.call.tz)
                exact = [c.item for c in full.candidates if c.exact]
                if not exact:
                    return ToolOutcome(_json({"ok": False, "status": "not_found", "hint": "Nothing matches exactly; nothing will be deleted."}), awaits_answer=True)
                if full.total_exact > MAX_BULK_DELETE:
                    return _err(f"more than {MAX_BULK_DELETE} items match; ask the user to narrow it down")
                if full.total > full.total_exact:
                    return _err("some items match only approximately; ask the user to name them more precisely")
                items = exact
            else:
                got = self._target(db, a, "delete", op="delete")
                if isinstance(got, ToolOutcome):
                    return got
                items = [got[0]]
            if len(items) > MAX_BULK_DELETE:
                return _err(f"at most {MAX_BULK_DELETE} items at a time")
            # several delete calls in the same turn: one confirmation for all of them
            pending = STORE.pending_confirmation(self.ctx)
            seen = {it.uid for it in items}
            if pending is not None and pending.op == "delete_items" and pending.created_turn == self.call.turn_id:
                for uid, version in pending.targets:
                    if uid not in seen and (it := repo.get_by_uid(self.call.account_id, uid)) is not None:
                        items.append(it)
                        seen.add(uid)
            kinds = {it.kind for it in items}
            facts = "delete " + ("; ".join(summary(it, self.call.tz) for it in items) if len(items) <= 3
                                 else f"{len(items)} items: " + "; ".join(summary(it, self.call.tz) for it in items[:3]) + "; ...")
            return self._ask_confirmation("delete_items", [(it.uid, it.version) for it in items], {},
                                          facts, kinds.pop() if len(kinds) == 1 else "note", count=len(items))

    def _continue_on_screen(self, it: Item, question: str, body: dict[str, Any]) -> ToolOutcome:
        """The words fit several places inside one item (two lines with "milk", two Mihais): nothing changes
        here; the item opens on the watch with its microphone on and the question shown, and the item's own
        screen takes over (its answer applies at once). The request and the question go with it."""
        STORE.set_open(self.ctx, it, summary(it, self.call.tz), self.call.turn_id)
        self.ctx.edit_followup = {"question": question, "user_text": self.call.user_text, "uid": it.uid,
                                  "until": mono_now() + CLARIFY_TTL_S}
        view = device_full(it, self.call.tz)
        view.update(listen=True, question=question)
        out = ToolOutcome(_json({
            "ok": False, **body, "nothing_changed": True, "opens_on_watch": True,
            "hint": "Nothing was changed. Tell the user in one short sentence that it appears in several places and "
            "that you are opening it on the watch so they can choose there. Do not ask the question yourself.",
        }))
        out.open, out.open_uid = {"item": view}, it.uid
        return out

    def _ask_confirmation(self, op: str, targets: list[tuple[str, int]], payload: dict[str, Any], facts: str,
                          kind: str, count: int = 1) -> ToolOutcome:
        STORE.put_confirmation(self.ctx, PendingConfirmation(
            op=op, targets=targets, payload={**payload, "kind": kind}, facts=facts, mode=self.call.mode,
            edit_uid=None, created_turn=self.call.turn_id, expires_at=mono_now() + CONFIRM_TTL_S,
        ))
        return ToolOutcome(_json({
            "ok": True, "status": "confirmation_required", "nothing_deleted_yet": True, "count": count,
            "will_delete": facts,
            "hint": "Ask the user to confirm, saying concretely what will be deleted (in their language, no ids). "
            "The server waits for their answer; you cannot confirm it yourself.",
        }), awaits_answer=True)


# --- confirmed actions (called by the pipeline's confirmation gate, never by the model) ---------------


@dataclass
class ConfirmResult:
    status: str  # done | changed | missing | error
    facts: str
    open: dict[str, Any] | None = None
    open_uid: str | None = None
    new_pending: bool = False


def execute_confirmation(call: ToolCallCtx, pc: PendingConfirmation) -> ConfirmResult:
    """Run a consumed confirmation: revalidated by uid + version inside the write (all or nothing),
    recorded in voice_operations in the same transaction."""
    key = op_key(call.account_id, call.device_id, call.session_id, call.turn_id, "confirm", {"id": pc.id})
    op = VoiceOperation(op_key=key, account_id=call.account_id, device_id=call.device_id,
                        session_id=call.session_id, turn_id=call.turn_id, tool="confirm",
                        summary=pc.facts[:300], result="{}")
    ctx = STORE.get(call.account_id, call.device_id, call.session_id)
    with session_scope() as db:
        repo = ItemRepo(db)
        if VoiceOpRepo(db).get(key) is not None:
            return ConfirmResult("done", "already done")
        try:
            if pc.op == "delete_items":
                repo.delete_exact(call.account_id, pc.targets, op=op)
                STORE.clear_open(ctx)
                # Nothing to show after a deletion (showing what is missing makes no sense): stay on the dialog.
                return ConfirmResult("done", f"deleted: {pc.facts[len('delete '):]}")
            uid, version = pc.targets[0]
            it = repo.get_by_uid(call.account_id, uid)
            if it is None:
                raise ItemConflictError(missing=[uid])
            if pc.op == "note_ops":
                it2 = repo.set_note_text(it, pc.payload["text"], expected_version=version, op=op)
                view = device_full(it2, call.tz)
                view["changed_line"] = pc.payload.get("highlight")
                STORE.refresh_version(ctx, it2.uid, it2.version)
                if not pc.payload.get("opens", True):  # only lines deleted: stay on the dialog
                    return ConfirmResult("done", f"done: {pc.facts}")
                return ConfirmResult("done", f"done: {pc.facts}", open={"item": view}, open_uid=it2.uid)
            if pc.op == "reminder_change":
                it2 = repo.update(it, **_unserial(pc.payload["changes"]), expected_version=version, op=op)
                STORE.refresh_version(ctx, it2.uid, it2.version)
                if not pc.payload.get("opens", True):  # only details removed: stay on the dialog
                    return ConfirmResult("done", f"done: {pc.facts}")
                return ConfirmResult("done", f"done: {pc.facts}", open={"item": device_full(it2, call.tz)}, open_uid=it2.uid)
        except ItemConflictError as exc:
            if exc.missing:
                return ConfirmResult("missing", "it no longer exists (deleted meanwhile); nothing else was deleted")
            return ConfirmResult("changed", "it changed meanwhile; nothing was deleted")
        except (ValueError, KeyError) as exc:
            log.warning("confirmed action failed: %s", exc)
            return ConfirmResult("error", "it could not be done; nothing was changed")
    return ConfirmResult("error", "unknown action; nothing was changed")


def reask_after_change(call: ToolCallCtx, pc: PendingConfirmation) -> PendingConfirmation | None:
    """The data changed between the question and the yes: a new confirmation about the current state
    (deletions of whole items only; line / detail changes are asked again from scratch)."""
    if pc.op != "delete_items":
        return None
    with session_scope() as db:
        repo = ItemRepo(db)
        items = [it for uid, _v in pc.targets if (it := repo.get_by_uid(call.account_id, uid)) is not None]
        if not items:
            return None
        facts = "delete " + "; ".join(summary(it, call.tz) for it in items)
        new = PendingConfirmation(op="delete_items", targets=[(it.uid, it.version) for it in items],
                                  payload=dict(pc.payload), facts=facts, mode=pc.mode, edit_uid=pc.edit_uid,
                                  created_turn=call.turn_id, expires_at=mono_now() + CONFIRM_TTL_S)
    STORE.put_confirmation(STORE.get(call.account_id, call.device_id, call.session_id), new)
    return new


# --- helpers ---------------------------------------------------------------------------------------


class AskUser(ValueError):
    """Something the user has to answer before the operation can run."""


class AmbiguousLine(ValueError):
    def __init__(self, lines: list[dict[str, Any]]) -> None:
        super().__init__("several lines match")
        self.lines = lines


def _clean(a: dict[str, Any]) -> dict[str, Any]:
    """Never trust identity or authorization fields from the model."""
    return {k: v for k, v in a.items() if k not in ("account_id", "device_id", "session_id", "confirmed", "resolved", "uid", "user_id")}


def _hint(status: str) -> str:
    return {
        "found": "Exactly one item matches.",
        "ambiguous": "Several items could match: ask the user which one, using the differences; never pick one yourself.",
        "not_found": "Nothing matches: say so and ask for a detail. Nothing was changed.",
        "insufficient": "Ask what the item is about.",
    }[status]


def _split(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _split_any(value: Any) -> list[str]:
    return [str(p).strip() for p in value] if isinstance(value, list) else _split(str(value or ""))


def reminder_changes(it: Item, ch: dict[str, Any], tz: str) -> tuple[dict[str, Any], list[str]]:
    """The model's `changes` -> ItemRepo.update kwargs, and what they would remove (needs confirmation).
    Unspecified fields stay; moving the start keeps the duration (ItemRepo)."""
    kw: dict[str, Any] = {}
    removed: list[str] = []
    if ch.get("text"):
        kw["text"] = str(ch["text"])
    elif "text" in ch:
        raise ValueError("a reminder needs a text")
    if ch.get("due_local"):
        kw["due_at"] = parse_local_time(ch["due_local"], tz, "due_local")
    elif ch.get("date_local") or ch.get("time_local"):
        if it.due_at is None:
            raise ValueError("this reminder has no time yet: ask for date and time")
        due = it.due_at
        if ch.get("date_local"):
            due = keep_time_on_date(due, str(ch["date_local"]), tz)
        if ch.get("time_local"):
            due = keep_date_with_time(due, str(ch["time_local"]), tz)
        kw["due_at"] = due
    if isinstance(ch.get("done"), bool):
        kw["done"] = ch["done"]
    if ch.get("end_local"):
        kw["end_at"] = parse_local_time(ch["end_local"], tz, "end_local")
    if ch.get("notify_before_minutes"):
        kw["notify_before_min"] = int(ch["notify_before_minutes"])
    if ch.get("location"):
        kw["location"] = str(ch["location"])
    people = _split(it.participants)
    if isinstance(ch.get("participants"), list) and not (ch.get("add_participants") or ch.get("remove_participants")):
        # a full new list (older tool format): its difference - removed names still need a confirmation
        wanted = _split_any(ch["participants"])
        ch = {**ch, "add_participants": [p for p in wanted if normalize(p) not in {normalize(x) for x in people}],
              "remove_participants": [p for p in people if normalize(p) not in {normalize(x) for x in wanted}]}
        if not ch["add_participants"] and not ch["remove_participants"]:
            ch.pop("add_participants")
            ch.pop("remove_participants")
    if ch.get("add_participants") or ch.get("remove_participants"):
        add = _split_any(ch.get("add_participants"))
        drop = {normalize(p) for p in resolve_people(people, _split_any(ch.get("remove_participants")))}
        kept = [p for p in people if normalize(p) not in drop]
        new = kept + [p for p in add if normalize(p) not in {normalize(x) for x in kept}]
        kw["participants"] = _names(new)
        lost = [p for p in people if normalize(p) not in {normalize(x) for x in new}]
        if lost:
            removed.append("participant " + ", ".join(lost))
    # explicit removals, or empty values (a removal in disguise): both need a confirmation
    remove = {r for r in (ch.get("remove") or []) if r in REMOVABLE}
    if "location" in remove or _has_empty(ch, "location"):
        if it.location:
            kw["location"] = ""
            removed.append(f"place {it.location}")
    if "end" in remove or _has_empty(ch, "end_local"):
        if it.end_at:
            kw["end_at"] = None
            removed.append("end time")
    if "notify" in remove or _has_empty(ch, "notify_before_minutes"):
        if it.notify_before_min:
            kw["notify_before_min"] = None
            removed.append("advance alert")
    if "participants" in remove and people:
        kw["participants"] = ""
        removed.append("participants " + ", ".join(people))
    return kw, removed


class AmbiguousPerson(ValueError):
    def __init__(self, said: str, names: list[str]) -> None:
        super().__init__(f"{said!r} could be {', '.join(names)}: ask which one")
        self.said, self.names = said, names


def resolve_people(people: list[str], said: list[str]) -> list[str]:
    """The participants the user means ("Mihai" -> "Mihai Pop"). Several fit -> AmbiguousPerson (ask);
    none -> ValueError."""
    out = []
    for name in said:
        exact = [p for p in people if normalize(p) == normalize(name)]
        if exact:
            out.append(exact[0])
            continue
        words = normalize(name).split()
        fits = [p for p in people if words and all(best_match(w, normalize(p).split()) >= PREFIX for w in words)]
        if len(fits) > 1:
            raise AmbiguousPerson(name, fits)
        if not fits:
            raise ValueError(f"not among the participants: {name}")
        out.append(fits[0])
    return out


def _has_empty(ch: dict[str, Any], key: str) -> bool:
    return key in ch and ch[key] in ("", None, 0, [])


def note_ops_add_or_change(ops: Any) -> bool:
    """Does the command add or change a line (not only delete)? Only then the chat opens the note."""
    return isinstance(ops, list) and any(isinstance(o, dict) and o.get("op") not in ("delete", "clear") for o in ops)


def reminder_add_or_change(it: Item, kw: dict[str, Any]) -> bool:
    """Do the changes add or change something (not only remove a person, the place, the end, the alert)?
    Only then the chat opens the reminder."""
    old_people = {normalize(p) for p in _split(it.participants)}
    for k, v in kw.items():
        if k in ("location", "end_at", "notify_before_min") and v in ("", None):
            continue
        if k == "participants" and {normalize(p) for p in _split(v or "")} <= old_people:
            continue
        return True
    return False


def note_ops_destructive(ops: Any) -> bool:
    return isinstance(ops, list) and any(isinstance(o, dict) and o.get("op") in ("delete", "clear") for o in ops)


def resolve_line_matches(lines: list[str], ops: Any) -> list[dict[str, Any]]:
    """`match` (text of a line) -> its line number; `clear` -> delete every numbered line."""
    if not isinstance(ops, list) or not ops:
        raise ValueError("ops must be a non-empty list")
    out: list[dict[str, Any]] = []
    for o in ops:
        if not isinstance(o, dict):
            raise ValueError("bad operation")
        o = dict(o)
        if o.get("op") == "clear":
            out.extend({"op": "delete", "line": n} for n in range(1, len(lines)))
            continue
        m = o.pop("match", None)
        if m and not isinstance(o.get("line"), int) and o.get("op") in ("replace", "delete", "move"):
            words = normalize(str(m)).split()
            hits = [n for n, ln in enumerate(lines[1:], start=1) if words and all(w in normalize(ln).split() or any(x.startswith(w) for x in normalize(ln).split()) for w in words)]
            if not hits:
                raise ValueError(f"no line contains {m!r}")
            if len(hits) > 1:
                raise AmbiguousLine([{"line": n, "text": lines[n]} for n in hits])
            o["line"] = hits[0]
        out.append(o)
    if not out:
        raise ValueError("the note has no lines to delete")
    return out


# Command words that never identify a line ("delete the milk": only "milk" does).
_COMMAND_WORDS = {
    "sterge", "stergeo", "stergel", "scoate", "elimina", "inlocuieste", "schimba", "muta", "pune", "rand", "randul",
    "linia", "line", "delete", "remove", "replace", "change", "move", "put", "erase", "cu", "with", "in", "into",
    "to", "la", "sus", "jos", "top", "bottom", "dupa", "after", "before", "inainte", "primul", "first", "last",
}


def check_line_ambiguity(lines: list[str], ops: list[dict[str, Any]], user_text: str) -> None:
    """The model chose a line number, but the user named it by words that fit several lines ("delete the milk"
    with "Milk" on line 1 and "Whole milk" on line 10): raise AmbiguousLine so the user is asked which one.
    A number said by the user, or words that fit one line only, are trusted."""
    if not user_text or any(ch.isdigit() for ch in user_text):
        return
    said = [w for w in tokens(user_text) if w not in _COMMAND_WORDS]
    if not said:
        return
    for o in ops:
        if o.get("op") not in ("replace", "delete", "move") or not isinstance(o.get("line"), int):
            continue
        n = o["line"]
        if not 0 < n < len(lines):
            continue
        target_words = tokens(lines[n])
        common = [w for w in said if best_match(w, target_words) >= PREFIX]
        if not common:
            continue
        hits = [k for k in range(1, len(lines)) if all(best_match(w, tokens(lines[k])) >= PREFIX for w in common)]
        if len(hits) > 1:
            raise AmbiguousLine([{"line": k, "text": lines[k]} for k in hits])


def describe_deleted_lines(lines: list[str], ops: list[dict[str, Any]]) -> str:
    gone = [o["line"] for o in ops if o.get("op") == "delete" and isinstance(o.get("line"), int)]
    if len(gone) == len(lines) - 1 and len(gone) > 1:
        return f"all {len(gone)} lines"
    return ", ".join(f"line {n} «{lines[n]}»" for n in gone if 0 < n < len(lines))


def copy_due(src: Item, ch: dict[str, Any], tz: str) -> datetime:
    """When the copy of a reminder starts. A date alone keeps the original time of day."""
    if ch.get("due_local"):
        return parse_local_time(ch["due_local"], tz, "due_local")
    if not ch.get("date_local") and not ch.get("time_local"):
        raise AskUser("Nothing was copied: ask which day (and time, if different) the copy is for.")
    due = src.due_at or datetime.now(timezone.utc)
    if ch.get("date_local"):
        due = keep_time_on_date(due, str(ch["date_local"]), tz)
    if ch.get("time_local"):
        due = keep_date_with_time(due, str(ch["time_local"]), tz)
    return due


def _serial(kw: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in kw.items()}


def _unserial(kw: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in kw.items():
        out[k] = datetime.fromisoformat(v) if k in ("due_at", "end_at") and isinstance(v, str) else v
    return out
