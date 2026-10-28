"""All-day reminders: one day or a range of days, no time of day. Stored at 09:00 local (when they alert);
overdue only once the (last) day is over; found by any day they cover; converted to and from a time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.items import device_snapshot, is_overdue, on_watch, parse_local
from tests.test_items import _account
from tests.voice_helpers import Voice

TZ = "Europe/Bucharest"
DAY = (datetime.now(ZoneInfo(TZ)) + timedelta(days=30)).date()


def _d(days: int = 0) -> str:
    return f"{DAY + timedelta(days=days):%Y-%m-%d}"


def _reminder(acc: int, text: str):
    with session_scope() as db:
        return next(it for it in ItemRepo(db).list(acc, "reminder") if it.text == text)


def _local(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M")


def test_create_all_day_by_voice_stores_09_00_without_notice() -> None:
    acc = _account()
    v = Voice(acc, TZ)
    body = v.body("item_create", {"kind": "reminder", "text": "Ziua Anei", "due_local": _d(),
                                  "all_day": True, "notify_before_minutes": 30})
    assert body["ok"]
    it = _reminder(acc, "Ziua Anei")
    assert it.all_day and _local(it.due_at) == f"{_d()} 09:00" and it.end_at is None
    assert it.notify_before_min is None
    row = device_snapshot([it], TZ)["reminders"][0]
    assert row["all_day"] is True and row["due_local"] == f"{_d()} 09:00"


def test_create_range_of_days_and_same_day_end_is_a_single_day() -> None:
    acc = _account()
    v = Voice(acc, TZ)
    assert v.body("item_create", {"kind": "reminder", "text": "Concediu", "due_local": _d(),
                                  "end_local": _d(4), "all_day": True})["ok"]
    it = _reminder(acc, "Concediu")
    assert _local(it.end_at) == f"{_d(4)} 09:00"
    assert v.body("item_create", {"kind": "reminder", "text": "Liber", "due_local": _d(),
                                  "end_local": _d(), "all_day": True})["ok"]
    assert _reminder(acc, "Liber").end_at is None


def test_a_date_alone_without_all_day_still_asks_for_a_time() -> None:
    acc = _account()
    body = Voice(acc, TZ).body("item_create", {"kind": "reminder", "text": "Dentist", "due_local": _d()})
    assert not body["ok"]


def test_overdue_and_on_watch_follow_the_last_day() -> None:
    acc = _account()
    today = datetime.now(ZoneInfo(TZ)).date()
    start = parse_local(f"{today - timedelta(days=2):%Y-%m-%d} 09:00", TZ)
    end = parse_local(f"{today:%Y-%m-%d} 09:00", TZ)
    with session_scope() as db:
        it = ItemRepo(db).create(acc, "reminder", "Târg", start, end, all_day=True)
    now = datetime.now(timezone.utc)
    assert not is_overdue(it, now) and on_watch(it, TZ, now)  # its last day is today
    tomorrow = parse_local(f"{today + timedelta(days=1):%Y-%m-%d} 00:30", TZ)
    assert is_overdue(it, tomorrow) and not on_watch(it, TZ, tomorrow)


def test_find_by_any_day_of_the_range_but_not_by_a_time() -> None:
    acc = _account()
    v = Voice(acc, TZ)
    v.body("item_create", {"kind": "reminder", "text": "Concediu", "due_local": _d(), "end_local": _d(4),
                           "all_day": True})
    assert v.body("item_find", {"kind": "reminder", "date": _d(2)})["status"] == "found"
    assert v.body("item_find", {"kind": "reminder", "date": _d(5)})["status"] == "not_found"
    assert v.body("item_find", {"kind": "reminder", "date": _d(2), "time": "10:00"})["status"] == "not_found"


def test_move_all_day_keeps_its_length_and_a_time_makes_it_timed() -> None:
    acc = _account()
    v = Voice(acc, TZ)
    v.body("item_create", {"kind": "reminder", "text": "Concediu", "due_local": _d(), "end_local": _d(2),
                           "all_day": True})
    target = {"query": {"kind": "reminder", "text": "concediu"}}
    assert v.body("item_update", {"target": target, "changes": {"date_local": _d(7)}})["ok"]
    it = _reminder(acc, "Concediu")
    assert it.all_day and _local(it.due_at) == f"{_d(7)} 09:00" and _local(it.end_at) == f"{_d(9)} 09:00"
    assert v.body("item_update", {"target": target, "changes": {"time_local": "14:30"}})["ok"]
    it = _reminder(acc, "Concediu")
    assert not it.all_day and _local(it.due_at) == f"{_d(7)} 14:30" and it.end_at is None


def test_timed_to_all_day_drops_the_time_range() -> None:
    acc = _account()
    v = Voice(acc, TZ)
    v.body("item_create", {"kind": "reminder", "text": "Ședință", "due_local": f"{_d()} 10:00",
                           "end_local": f"{_d()} 11:00", "notify_before_minutes": 15})
    target = {"query": {"kind": "reminder", "text": "sedinta"}}
    assert v.body("item_update", {"target": target, "changes": {"all_day": True}})["ok"]
    it = _reminder(acc, "Ședință")
    assert it.all_day and _local(it.due_at) == f"{_d()} 09:00" and it.end_at is None
    assert it.notify_before_min is None
    # back to a time needs the time
    assert not v.body("item_update", {"target": target, "changes": {"all_day": False}})["ok"]


def test_copy_all_day_to_another_day_stays_all_day() -> None:
    acc = _account()
    v = Voice(acc, TZ)
    v.body("item_create", {"kind": "reminder", "text": "Piață", "due_local": _d(), "all_day": True})
    body = v.body("item_duplicate", {"target": {"query": {"kind": "reminder", "text": "piata"}},
                                     "changes": {"date_local": _d(7)}})
    assert body["ok"]
    with session_scope() as db:
        copies = sorted((it for it in ItemRepo(db).list(acc, "reminder")), key=lambda i: i.due_at)
    assert [c.all_day for c in copies] == [True, True] and _local(copies[1].due_at) == f"{_d(7)} 09:00"
