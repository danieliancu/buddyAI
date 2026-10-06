"""Repository layer: all DB access for business logic goes through here (portable SQLAlchemy only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import delete as sa_delete, func, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db.models import (
    AdminUser,
    Conversation,
    Device,
    DeviceIssue,
    DeviceSettingsRow,
    FirmwareRelease,
    Item,
    Persona,
    PricingRule,
    Turn,
    UsageRecord,
    VoiceOperation,
    utcnow,
)
from app.device_settings import DeviceSettings, merge


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; treat them as UTC.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class AdminRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    def count(self) -> int:
        return self.s.exec(select(func.count()).select_from(AdminUser)).one()

    def get(self, username: str) -> AdminUser | None:
        return self.s.exec(select(AdminUser).where(AdminUser.username == username)).first()

    def create(self, username: str, password_hash: str) -> AdminUser:
        user = AdminUser(username=username, password_hash=password_hash)
        self.s.add(user)
        self.s.commit()
        self.s.refresh(user)
        return user


class DeviceRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    def get(self, device_id: str) -> Device | None:
        return self.s.get(Device, device_id)

    def by_token_hash(self, token_hash: str) -> Device | None:
        return self.s.exec(
            select(Device).where(Device.token_hash == token_hash, col(Device.revoked_at).is_(None))
        ).first()

    def list(self, account_id: int | None = None) -> Sequence[Device]:
        """Paired devices; restricted to one account when account_id is given."""
        q = select(Device).where(col(Device.token_hash).is_not(None))
        if account_id is not None:
            q = q.where(Device.account_id == account_id)
        return self.s.exec(q).all()

    def owned(self, device_id: str, account_id: int) -> Device | None:
        """The device if (and only if) it is paired and belongs to the account."""
        dev = self.get(device_id)
        return dev if dev is not None and dev.account_id == account_id and dev.token_hash else None

    def pair(
        self, device_id: str, token_hash: str, name: str, hw_model: str, fw: str, account_id: int | None = None
    ) -> Device:
        dev = self.get(device_id) or Device(id=device_id)
        if dev.account_id is not None and dev.account_id != account_id:
            # New owner: nothing personal from the previous owner carries over.
            self._forget_owner_data(device_id)
        dev.account_id = account_id
        dev.name = name or dev.name
        dev.hw_model = hw_model
        dev.fw_version = fw
        dev.token_hash = token_hash
        dev.paired_at = utcnow()
        dev.revoked_at = None
        self.s.add(dev)
        self.s.commit()
        self.s.refresh(dev)
        return dev

    def revoke(self, device_id: str, unassign: bool = False) -> Device | None:
        dev = self.get(device_id)
        if dev:
            dev.revoked_at = utcnow()
            dev.token_hash = None
            if unassign:
                self._forget_owner_data(device_id)
                dev.account_id = None
            self.s.add(dev)
            self.s.commit()
        return dev

    def _forget_owner_data(self, device_id: str) -> None:
        """History and settings (which may hold personal text) are erased when the owner changes."""
        ConversationRepo(self.s).delete_device_history(device_id, commit=False)
        row = self.s.get(DeviceSettingsRow, device_id)
        if row is not None:
            self.s.delete(row)
            self.s.flush()

    def assign(self, device_id: str, account_id: int | None) -> Device | None:
        """Operator: move a watch to another account (or to stock). Old history is erased."""
        dev = self.get(device_id)
        if dev is None:
            return None
        if dev.account_id != account_id:
            self._forget_owner_data(device_id)
            dev.account_id = account_id
            self.s.add(dev)
            self.s.commit()
            self.s.refresh(dev)
        return dev

    def touch(self, device_id: str, **fields: Any) -> None:
        dev = self.get(device_id)
        if not dev:
            return
        for k, v in fields.items():
            setattr(dev, k, v)
        dev.last_seen_at = utcnow()
        self.s.add(dev)
        self.s.commit()

    def rename(self, device_id: str, name: str) -> Device | None:
        dev = self.get(device_id)
        if dev:
            dev.name = name
            self.s.add(dev)
            self.s.commit()
            self.s.refresh(dev)
        return dev


class SettingsRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    def get(self, device_id: str) -> tuple[DeviceSettings, int]:
        row = self.s.get(DeviceSettingsRow, device_id)
        if row is None:
            return DeviceSettings(), 0
        return DeviceSettings.model_validate(row.data), row.version

    def ensure(self, device_id: str) -> tuple[DeviceSettings, int]:
        row = self.s.get(DeviceSettingsRow, device_id)
        if row is None:
            settings = DeviceSettings()
            row = DeviceSettingsRow(device_id=device_id, version=1, data=settings.model_dump())
            self.s.add(row)
            self.s.commit()
        return DeviceSettings.model_validate(row.data), row.version

    def update(self, device_id: str, changes: dict[str, Any]) -> tuple[DeviceSettings, int]:
        current, _ = self.ensure(device_id)
        new = merge(current, changes)
        row = self.s.get(DeviceSettingsRow, device_id)
        assert row is not None
        row.data = new.model_dump()
        row.version += 1
        row.updated_at = utcnow()
        self.s.add(row)
        self.s.commit()
        return new, row.version


class PersonaRepo:
    DEFAULTS = [
        (
            "Ola",
            "You are Ola, a warm, concise and helpful voice assistant living in a smartwatch.",
        ),
        (
            "Coach",
            "You are an energetic personal coach. Motivate the user and give practical, short advice.",
        ),
    ]

    def __init__(self, s: Session) -> None:
        self.s = s

    def seed(self) -> None:
        if self.s.exec(select(func.count()).select_from(Persona)).one():
            return
        for i, (name, prompt) in enumerate(self.DEFAULTS):
            self.s.add(Persona(name=name, system_prompt=prompt, is_default=(i == 0)))
        self.s.commit()

    def list(self, account_id: int | None = None, system_only: bool = False) -> Sequence[Persona]:
        """All personas (operator), system personas only, or system + the account's own."""
        q = select(Persona)
        if system_only:
            q = q.where(col(Persona.account_id).is_(None))
        elif account_id is not None:
            q = q.where((col(Persona.account_id).is_(None)) | (Persona.account_id == account_id))
        return self.s.exec(q.order_by(Persona.id)).all()

    def visible_to(self, persona_id: int, account_id: int | None) -> bool:
        p = self.s.get(Persona, persona_id)
        return p is not None and (p.account_id is None or p.account_id == account_id)

    def get(self, persona_id: int | None, account_id: int | None = None) -> Persona | None:
        """The requested persona if visible to the account, else the system default."""
        if persona_id is not None:
            p = self.s.get(Persona, persona_id)
            if p and (p.account_id is None or p.account_id == account_id):
                return p
        return self.s.exec(
            select(Persona).where(Persona.is_default == True, col(Persona.account_id).is_(None))  # noqa: E712
        ).first()

    def chat_title(self, persona_id: int | None, account_id: int | None = None) -> str:
        """Title of the watch's dialog screen: the active persona's name, "" for the default persona."""
        p = self.get(persona_id, account_id)
        return "" if p is None or p.is_default else p.name[:40]

    def upsert(
        self, persona_id: int | None, name: str, prompt: str, is_default: bool, account_id: int | None = None
    ) -> Persona:
        """Create/update. Customer personas (account_id set) can never be the system default."""
        p = self.s.get(Persona, persona_id) if persona_id else None
        p = p or Persona(name=name, system_prompt=prompt, account_id=account_id)
        p.name, p.system_prompt = name, prompt
        if account_id is not None:
            is_default = False
        if is_default:
            for other in self.list(system_only=True):
                if other.is_default and other.id != p.id:
                    other.is_default = False
                    self.s.add(other)
        p.is_default = is_default
        self.s.add(p)
        self.s.commit()
        self.s.refresh(p)
        return p

    def delete(self, persona_id: int) -> bool:
        p = self.s.get(Persona, persona_id)
        if not p or p.is_default:
            return False
        for row in self.s.exec(select(DeviceSettingsRow)).all():
            if row.data.get("persona_id") == persona_id:
                row.data = {**row.data, "persona_id": None}  # reassign: JSON column change tracking
                row.version += 1
                self.s.add(row)
        self.s.delete(p)
        self.s.commit()
        return True


class ConversationRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    def current(self, device_id: str, idle_minutes: int) -> Conversation:
        conv = self.s.exec(
            select(Conversation)
            .where(Conversation.device_id == device_id)
            .order_by(col(Conversation.last_activity_at).desc())
        ).first()
        now = utcnow()
        if conv is None or (now - _aware(conv.last_activity_at)) > timedelta(minutes=idle_minutes):
            conv = Conversation(device_id=device_id)
        conv.last_activity_at = now
        self.s.add(conv)
        self.s.commit()
        self.s.refresh(conv)
        return conv

    def history(self, conversation_id: int, limit_turns: int, account_id: int | None = None) -> list[Turn]:
        """Completed turns for the LLM context, only those of the current owner."""
        if limit_turns <= 0:
            return []
        owner = Turn.account_id == account_id if account_id is not None else col(Turn.account_id).is_(None)
        rows = self.s.exec(
            select(Turn)
            .where(Turn.conversation_id == conversation_id, Turn.status == "completed", owner)
            .order_by(col(Turn.id).desc())
            .limit(limit_turns)
        ).all()
        return list(reversed(rows))

    def list_for_device(self, device_id: str, limit: int = 50) -> Sequence[Conversation]:
        return self.s.exec(
            select(Conversation)
            .where(Conversation.device_id == device_id)
            .order_by(col(Conversation.last_activity_at).desc())
            .limit(limit)
        ).all()

    def turns(self, conversation_id: int, account_id: int | None = None) -> Sequence[Turn]:
        q = select(Turn).where(Turn.conversation_id == conversation_id)
        if account_id is not None:
            q = q.where(Turn.account_id == account_id)
        return self.s.exec(q.order_by(Turn.id)).all()

    def delete_device_history(self, device_id: str, commit: bool = True) -> int:
        turns = self.s.exec(select(Turn).where(Turn.device_id == device_id)).all()
        # memories stay (they are not history): only their link to these turns / conversations goes
        from app.memory.repo import MemoryRepo

        conv_ids = list(self.s.exec(select(Conversation.id).where(Conversation.device_id == device_id)).all())
        MemoryRepo(self.s).detach_turns([t.id for t in turns])
        MemoryRepo(self.s).detach_conversations(conv_ids)
        for usage in self.s.exec(
            select(UsageRecord).where(col(UsageRecord.turn_id).in_([t.id for t in turns]))
        ).all():
            usage.turn_id = None  # keep usage for billing, drop the link to content
            self.s.add(usage)
        for t in turns:
            self.s.delete(t)
        for c in self.s.exec(select(Conversation).where(Conversation.device_id == device_id)).all():
            self.s.delete(c)
        if commit:
            self.s.commit()
        else:
            self.s.flush()
        return len(turns)


class TurnRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    def create(self, **fields: Any) -> Turn:
        t = Turn(**fields)
        self.s.add(t)
        self.s.commit()
        self.s.refresh(t)
        return t

    def update(self, turn_db_id: int, **fields: Any) -> None:
        t = self.s.get(Turn, turn_db_id)
        if not t:
            return
        for k, v in fields.items():
            setattr(t, k, v)
        self.s.add(t)
        self.s.commit()

    def recent(self, since: datetime, device_id: str | None = None, account_id: int | None = None) -> Sequence[Turn]:
        q = select(Turn).where(Turn.created_at >= since)
        if device_id:
            q = q.where(Turn.device_id == device_id)
        if account_id is not None:
            q = q.where(Turn.account_id == account_id)
        return self.s.exec(q.order_by(Turn.id)).all()


class UsageRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    def add_many(self, records: list[UsageRecord]) -> None:
        self.s.add_all(records)
        self.s.commit()

    def since(
        self, since: datetime, device_id: str | None = None, account_id: int | None = None
    ) -> Sequence[UsageRecord]:
        q = select(UsageRecord).where(UsageRecord.created_at >= since)
        if device_id:
            q = q.where(UsageRecord.device_id == device_id)
        if account_id is not None:
            q = q.where(UsageRecord.account_id == account_id)
        return self.s.exec(q).all()


class PricingRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    def seed(self, defaults: list[dict[str, Any]]) -> None:
        existing = {(r.provider, r.model, r.unit) for r in self.list()}
        for d in defaults:
            if (d["provider"], d["model"], d["unit"]) not in existing:
                self.s.add(
                    PricingRule(
                        provider=d["provider"],
                        model=d["model"],
                        unit=d["unit"],
                        price_usd=d["price_usd"],
                        note=d.get("note", ""),
                    )
                )
        self.s.commit()

    def list(self) -> Sequence[PricingRule]:
        return self.s.exec(select(PricingRule).order_by(PricingRule.provider, PricingRule.model)).all()

    def update(self, rule_id: int, price_usd: float, note: str | None) -> PricingRule | None:
        r = self.s.get(PricingRule, rule_id)
        if r:
            r.price_usd = price_usd
            if note is not None:
                r.note = note
            r.updated_at = utcnow()
            self.s.add(r)
            self.s.commit()
            self.s.refresh(r)
        return r


class FirmwareRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    def add(self, rel: FirmwareRelease) -> FirmwareRelease:
        self.s.add(rel)
        self.s.commit()
        self.s.refresh(rel)
        return rel

    def list(self) -> Sequence[FirmwareRelease]:
        return self.s.exec(select(FirmwareRelease).order_by(col(FirmwareRelease.id).desc())).all()

    def get(self, rel_id: int) -> FirmwareRelease | None:
        return self.s.get(FirmwareRelease, rel_id)


class IssueRepo:
    def __init__(self, s: Session) -> None:
        self.s = s

    def add(self, issue: DeviceIssue) -> DeviceIssue:
        self.s.add(issue)
        self.s.commit()
        self.s.refresh(issue)
        return issue

    def list(
        self, device_id: str | None = None, kind: str | None = None, problems_only: bool = False, limit: int = 200
    ) -> Sequence[DeviceIssue]:
        q = select(DeviceIssue)
        if device_id:
            q = q.where(DeviceIssue.device_id == device_id)
        if kind:
            q = q.where(DeviceIssue.kind == kind)
        if problems_only:
            q = q.where(DeviceIssue.severity != "info")
        return self.s.exec(q.order_by(col(DeviceIssue.id).desc()).limit(limit)).all()

    def clear(self, device_id: str | None = None) -> int:
        q = select(DeviceIssue)
        if device_id:
            q = q.where(DeviceIssue.device_id == device_id)
        rows = self.s.exec(q).all()
        for r in rows:
            self.s.delete(r)
        self.s.commit()
        return len(rows)


class ItemLimitError(Exception):
    """The account already has the maximum number of items of this kind."""


class ItemTextError(ValueError):
    """Empty text, or longer than the kind allows."""


class ItemTimeError(ValueError):
    """A reminder's end time is not after its start, or its advance notice is out of range."""


class ItemConflictError(Exception):
    """A versioned write found the item changed (`changed`) or gone (`missing`) since it was read."""

    def __init__(self, missing: list[str] | None = None, changed: list[str] | None = None) -> None:
        self.missing, self.changed = missing or [], changed or []
        super().__init__(f"items changed {self.changed} / missing {self.missing}")


_KEEP: Any = object()  # ItemRepo.update: leave the end time as it is

# Columns written by a content change (everything a user can edit, plus the state reset by a reschedule).
_CONTENT_COLS = ("text", "due_at", "end_at", "notify_before_min", "early_fired_at", "location", "participants",
                 "pinned", "fired_at", "done_at", "updated_at")


class ItemRepo:
    """Notes and reminders. Numbers are per (account, kind); a new item takes the lowest free number."""

    KINDS = ("note", "reminder")
    MAX_PER_KIND = 100
    TEXT_MAX = {"note": 10000, "reminder": 80}

    def __init__(self, s: Session) -> None:
        self.s = s

    def list(self, account_id: int, kind: str | None = None) -> list[Item]:
        q = select(Item).where(Item.account_id == account_id)
        if kind:
            q = q.where(Item.kind == kind)
        items = list(self.s.exec(q.order_by(Item.kind, Item.number)).all())
        for it in items:
            self._fix(it)
        return items

    def get_by_uid(self, account_id: int, uid: str) -> Item | None:
        """The item with this stable id, only if it belongs to the account."""
        it = self.s.exec(select(Item).where(Item.account_id == account_id, Item.uid == uid)).first()
        return self._fix(it) if it else None

    def get(self, account_id: int, kind: str, number: int) -> Item | None:
        it = self.s.exec(
            select(Item).where(Item.account_id == account_id, Item.kind == kind, Item.number == number)
        ).first()
        return self._fix(it) if it else None

    def next_number(self, account_id: int, kind: str) -> int:
        used = set(self.s.exec(select(Item.number).where(Item.account_id == account_id, Item.kind == kind)).all())
        if len(used) >= self.MAX_PER_KIND:
            raise ItemLimitError(kind)
        n = 1
        while n in used:
            n += 1
        return n

    @classmethod
    def check_text(cls, kind: str, text: str) -> str:
        text = text.strip()
        if not text:
            raise ItemTextError("text is empty")
        if len(text) > cls.TEXT_MAX[kind]:
            raise ItemTextError(f"{kind} text is {len(text)} characters; the limit is {cls.TEXT_MAX[kind]}")
        return text

    @staticmethod
    def check_end(due_at: datetime | None, end_at: datetime | None) -> datetime | None:
        if end_at is not None and (due_at is None or end_at <= due_at):
            raise ItemTimeError("the end time must be after the start time")
        return end_at

    NOTIFY_MAX_MIN = 7 * 24 * 60

    @classmethod
    def check_notify(cls, minutes: int | None) -> int | None:
        """Advance notice in minutes; 0 / None = none."""
        if not minutes:
            return None
        if not 1 <= minutes <= cls.NOTIFY_MAX_MIN:
            raise ItemTimeError(f"the advance notice must be 1..{cls.NOTIFY_MAX_MIN} minutes")
        return minutes

    EXTRA_MAX = {"location": 120, "participants": 200}

    @classmethod
    def check_extra(cls, field: str, value: str | None) -> str | None:
        """Location / participants: trimmed, "" -> None, length-checked."""
        value = " ".join((value or "").split())
        if not value:
            return None
        if len(value) > cls.EXTRA_MAX[field]:
            raise ItemTextError(f"{field} is {len(value)} characters; the limit is {cls.EXTRA_MAX[field]}")
        return value

    def create(
        self,
        account_id: int,
        kind: str,
        text: str,
        due_at: datetime | None = None,
        end_at: datetime | None = None,
        notify_before_min: int | None = None,
        location: str | None = None,
        participants: str | None = None,
        op: VoiceOperation | None = None,
    ) -> Item:
        """`op`: the voice operation record, committed together with the new item."""
        text = self.check_text(kind, text)
        end_at = self.check_end(due_at, end_at) if kind == "reminder" else None
        notify_before_min = self.check_notify(notify_before_min) if kind == "reminder" else None
        location = self.check_extra("location", location) if kind == "reminder" else None
        participants = self.check_extra("participants", participants) if kind == "reminder" else None
        for attempt in range(2):
            it = Item(
                account_id=account_id,
                kind=kind,
                number=self.next_number(account_id, kind),
                text=text,
                due_at=due_at if kind == "reminder" else None,
                end_at=end_at,
                notify_before_min=notify_before_min,
                location=location,
                participants=participants,
            )
            self.s.add(it)
            if op is not None:
                op.summary = op.summary or f"{kind}: {text[:120]}"
                self.s.add(op)
            try:
                self.s.commit()
            except IntegrityError:  # another writer took the same number (or the same voice operation)
                self.s.rollback()
                if attempt or (op is not None and VoiceOpRepo(self.s).get(op.op_key)):
                    raise
                continue
            self.s.refresh(it)
            return self._fix(it)
        raise RuntimeError("unreachable")

    def update(
        self,
        it: Item,
        text: str | None = None,
        due_at: datetime | None = None,
        done: bool | None = None,
        end_at: datetime | None = _KEEP,
        notify_before_min: int | None = _KEEP,
        location: str | None = _KEEP,
        participants: str | None = _KEEP,
        pinned: bool | None = None,
        expected_version: int | None = None,
        op: VoiceOperation | None = None,
    ) -> Item:
        """`end_at`: a new end time, None to remove it, or leave it out to keep the range's length when
        only the start moves. `expected_version`: write only if the item is still at that version
        (ItemConflictError otherwise) - voice changes never overwrite a change made meanwhile."""
        if expected_version is not None:
            if it in self.s:
                self.s.expunge(it)  # computed on a detached copy, written by one conditional UPDATE
        if text is not None:
            it.text = self.check_text(it.kind, text)
        if it.kind == "reminder":
            new_due = due_at if due_at is not None else it.due_at
            if end_at is _KEEP:
                end_at = it.end_at + (new_due - it.due_at) if it.end_at and it.due_at and new_due else it.end_at
            it.end_at = self.check_end(new_due, end_at)
            if notify_before_min is not _KEEP:
                notify = self.check_notify(notify_before_min)
                if notify != it.notify_before_min:
                    it.notify_before_min = notify
                    it.early_fired_at = None  # a new advance notice is delivered again
            if location is not _KEEP:
                it.location = self.check_extra("location", location)
            if participants is not _KEEP:
                it.participants = self.check_extra("participants", participants)
        if it.kind == "reminder" and due_at is not None and due_at != it.due_at:
            it.due_at = due_at
            it.fired_at = None  # a rescheduled reminder fires again
            it.early_fired_at = None  # ...its advance notice too
            it.done_at = None  # ...and is open again
        if it.kind == "note" and pinned is not None:
            it.pinned = pinned
        if it.kind == "reminder" and done is not None:
            it.done_at = (it.done_at or utcnow()) if done else None
        it.updated_at = utcnow()
        return self._save(it, expected_version, op)

    def set_note_text(
        self, it: Item, text: str, expected_version: int | None = None, op: VoiceOperation | None = None
    ) -> Item:
        """Note edit mode: the new text may be empty (every line deleted)."""
        if len(text) > self.TEXT_MAX["note"]:
            raise ItemTextError(f"note text is {len(text)} characters; the limit is {self.TEXT_MAX['note']}")
        if expected_version is not None and it in self.s:
            self.s.expunge(it)
        it.text = text
        it.updated_at = utcnow()
        return self._save(it, expected_version, op)

    def _save(self, it: Item, expected_version: int | None, op: VoiceOperation | None = None) -> Item:
        if expected_version is None:
            it.version = (it.version or 1) + 1
            self.s.add(it)
            if op is not None:
                self.s.add(op)
            self.s.commit()
            self.s.refresh(it)
            return self._fix(it)
        res = self.s.execute(
            sa_update(Item)
            .where(Item.id == it.id, Item.account_id == it.account_id, Item.version == expected_version)
            .values(**{c: getattr(it, c) for c in _CONTENT_COLS}, version=expected_version + 1)
        )
        if res.rowcount != 1:
            self.s.rollback()
            exists = self.get_by_uid(it.account_id, it.uid) is not None
            raise ItemConflictError(changed=[it.uid] if exists else [], missing=[] if exists else [it.uid])
        if op is not None:
            self.s.add(op)
        self.s.commit()
        fresh = self.get_by_uid(it.account_id, it.uid)
        assert fresh is not None
        return fresh

    def duplicate(self, src: Item, op: VoiceOperation | None = None, **overrides: Any) -> Item:
        """A new item copied from `src` (own uid and number, not pinned, not completed, never fired).
        `overrides`: text, due_at, end_at, notify_before_min, location, participants."""
        fields: dict[str, Any] = {
            "text": src.text,
            "due_at": src.due_at,
            "end_at": src.end_at,
            "notify_before_min": src.notify_before_min,
            "location": src.location,
            "participants": src.participants,
        }
        fields.update(overrides)
        if src.kind == "note" and not fields["text"].strip():
            raise ItemTextError("text is empty")
        return self.create(src.account_id, src.kind, op=op, **fields)

    def delete(self, it: Item) -> None:
        self.s.delete(it)
        self.s.commit()

    def delete_exact(self, account_id: int, targets: list[tuple[str, int]], op: VoiceOperation | None = None) -> int:
        """Delete exactly these (uid, version) items of the account in one transaction: all or nothing.
        ItemConflictError if any is gone or changed since (then nothing is deleted)."""
        missing, changed = [], []
        for uid, version in targets:
            res = self.s.execute(
                sa_delete(Item).where(Item.account_id == account_id, Item.uid == uid, Item.version == version)
            )
            if res.rowcount != 1:
                (changed if self._exists(account_id, uid) else missing).append(uid)
        if missing or changed:
            self.s.rollback()
            raise ItemConflictError(missing=missing, changed=changed)
        if op is not None:
            self.s.add(op)
        self.s.commit()
        return len(targets)

    def _exists(self, account_id: int, uid: str) -> bool:
        return self.s.exec(select(Item.id).where(Item.account_id == account_id, Item.uid == uid)).first() is not None

    def due(self, now: datetime, since: datetime | None = None, account_id: int | None = None) -> list[Item]:
        """Reminders that are due and not yet delivered (optionally only those due after `since`)."""
        q = select(Item).where(
            Item.kind == "reminder",
            col(Item.due_at).is_not(None),
            Item.due_at <= now,
            col(Item.fired_at).is_(None),
            col(Item.done_at).is_(None),  # completed early: never fires
        )
        if since is not None:
            q = q.where(Item.due_at >= since)
        if account_id is not None:
            q = q.where(Item.account_id == account_id)
        return [self._fix(it) for it in self.s.exec(q.order_by(Item.due_at)).all()]

    def due_early(self, now: datetime, account_id: int | None = None) -> list[Item]:
        """Reminders whose advance notice is due: notice time reached, start still ahead, not yet
        delivered, not completed."""
        q = select(Item).where(
            Item.kind == "reminder",
            col(Item.notify_before_min).is_not(None),
            col(Item.due_at).is_not(None),
            Item.due_at > now,
            Item.due_at <= now + timedelta(minutes=self.NOTIFY_MAX_MIN),
            col(Item.early_fired_at).is_(None),
            col(Item.done_at).is_(None),
        )
        if account_id is not None:
            q = q.where(Item.account_id == account_id)
        out = []
        for it in self.s.exec(q.order_by(Item.due_at)).all():
            self._fix(it)
            if it.due_at - timedelta(minutes=it.notify_before_min or 0) <= now:
                out.append(it)
        return out

    # Delivery marks are not content changes: they do not bump the version (a reminder firing must not
    # invalidate a pending voice confirmation about it).
    def mark_early_fired(self, it: Item) -> None:
        it.early_fired_at = utcnow()
        self.s.add(it)
        self.s.commit()

    def mark_fired(self, it: Item) -> None:
        it.fired_at = utcnow()
        self.s.add(it)
        self.s.commit()

    @staticmethod
    def _fix(it: Item) -> Item:
        it.due_at = _aware(it.due_at)
        it.end_at = _aware(it.end_at)
        it.early_fired_at = _aware(it.early_fired_at)
        it.fired_at = _aware(it.fired_at)
        it.done_at = _aware(it.done_at)
        it.created_at = _aware(it.created_at)
        it.updated_at = _aware(it.updated_at)
        return it


class VoiceOpRepo:
    """Durable record of voice changes (see VoiceOperation): idempotency across retries and reconnects."""

    KEEP_DAYS = 7

    def __init__(self, s: Session) -> None:
        self.s = s

    def get(self, op_key: str) -> VoiceOperation | None:
        return self.s.exec(select(VoiceOperation).where(VoiceOperation.op_key == op_key)).first()

    def mark_reported(self, session_id: str, turn_id: int) -> None:
        rows = self.s.exec(
            select(VoiceOperation).where(
                VoiceOperation.session_id == session_id, VoiceOperation.turn_id == turn_id, VoiceOperation.reported == False  # noqa: E712
            )
        ).all()
        for r in rows:
            r.reported = True
            self.s.add(r)
        if rows:
            self.s.commit()

    def unreported(self, account_id: int, device_id: str, since: datetime) -> list[VoiceOperation]:
        """Changes saved by voice whose turn did not end normally (the user may not have heard about them)."""
        return list(
            self.s.exec(
                select(VoiceOperation)
                .where(
                    VoiceOperation.account_id == account_id,
                    VoiceOperation.device_id == device_id,
                    VoiceOperation.reported == False,  # noqa: E712
                    VoiceOperation.created_at >= since,
                )
                .order_by(VoiceOperation.id)
            ).all()
        )

    def acknowledge(self, ids: list[int]) -> None:
        """The next turn was told about these changes."""
        for r in self.s.exec(select(VoiceOperation).where(col(VoiceOperation.id).in_(ids))).all():
            r.reported = True
            self.s.add(r)
        self.s.commit()

    def prune(self, now: datetime) -> None:
        self.s.exec(sa_delete(VoiceOperation).where(VoiceOperation.created_at < now - timedelta(days=self.KEEP_DAYS)))  # type: ignore[call-overload]
        self.s.commit()
