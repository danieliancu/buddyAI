"""Finding items by what the user says (app.item_search): content, people, place, day; hard filters; fuzzy
matches only proposed; account scope; past / completed only when asked."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.item_search import ItemQuery, normalize, search
from tests.test_items import _account

TZ = "Europe/Bucharest"
NOW = datetime(2030, 5, 6, 9, 0, tzinfo=timezone.utc)  # Monday 2030-05-06, 12:00 in Bucharest


def _items(acc: int):
    with session_scope() as db:
        return ItemRepo(db).list(acc)


def _q(**kw) -> ItemQuery:
    return ItemQuery.from_args(kw)


def _setup() -> int:
    acc = _account()
    with session_scope() as db:
        r = ItemRepo(db)
        r.create(acc, "note", "Cumpărături\nlapte bio\npâine\nmâncare pentru pisică")
        r.create(acc, "note", "Renovare bucătărie\nfaianță\nchiuvetă")
        # Monday 2030-05-06 10:00 local and Thursday 2030-05-09 15:00 local (UTC+3)
        r.create(acc, "reminder", "Ședință cu Ștefan", datetime(2030, 5, 6, 7, 0, tzinfo=timezone.utc),
                 participants="Ștefan", location="Studio")
        r.create(acc, "reminder", "Întâlnire cu Ștefan", datetime(2030, 5, 9, 12, 0, tzinfo=timezone.utc),
                 participants="Ștefan, Ana", location="Biroul central")
        r.create(acc, "reminder", "Dentist", datetime(2030, 5, 7, 6, 30, tzinfo=timezone.utc), location="Clinica Zâmbet")
    return acc


def test_normalize_ignores_case_spacing_and_diacritics() -> None:
    assert normalize("  ȘTEFAN,   Mâncare  ") == "stefan mancare"
    assert normalize("Straße Łódź") == "strasse lodz"


def test_note_found_by_word_only_in_inner_line() -> None:
    acc = _setup()
    res = search(_items(acc), _q(text="mancare pisica"), TZ, NOW)
    assert res.status == "found" and res.candidates[0].item.text.startswith("Cumpărături")
    assert res.candidates[0].line == 3  # "mâncare pentru pisică" is numbered line 3


def test_reminder_found_by_person_place_and_date() -> None:
    acc = _setup()
    res = search(_items(acc), _q(kind="reminder", person=["Ana"], place="biroul", date="2030-05-09"), TZ, NOW)
    assert res.status == "found" and res.candidates[0].item.text == "Întâlnire cu Ștefan"
    assert any("person" in r for r in res.candidates[0].reasons)


def test_match_ignores_diacritics_case_spacing() -> None:
    acc = _setup()
    for spoken in ("stefan", "ȘTEFAN", "  ștefan  "):
        res = search(_items(acc), _q(kind="reminder", person=[spoken]), TZ, NOW)
        assert res.total_exact == 2
    assert search(_items(acc), _q(text="LAPTE  BIO"), TZ, NOW).status == "found"


def test_explicit_weekday_is_a_hard_filter() -> None:
    acc = _setup()
    res = search(_items(acc), _q(kind="reminder", person=["Stefan"], weekday="thu"), TZ, NOW)
    assert res.status == "found" and res.candidates[0].item.text == "Întâlnire cu Ștefan"
    # "Ștefan on Friday": nothing - the Monday / Thursday ones are never offered instead
    assert search(_items(acc), _q(kind="reminder", person=["Stefan"], weekday="fri"), TZ, NOW).status == "not_found"


def test_two_people_with_the_same_name_are_ambiguous() -> None:
    acc = _setup()
    res = search(_items(acc), _q(kind="reminder", person=["Ștefan"], purpose="change"), TZ, NOW)
    assert res.status == "ambiguous" and res.total_exact == 2 and "day" in res.differences


def test_fuzzy_only_proposes_candidates() -> None:
    acc = _setup()
    # "Stefen" (a transcription slip) is close to Ștefan but not an exact match: proposed, never found
    res = search(_items(acc), _q(kind="reminder", person=["Stefen"], weekday="thu"), TZ, NOW)
    assert res.status == "ambiguous" and res.total_exact == 0 and res.candidates and not res.candidates[0].exact


def test_exact_match_with_a_plausible_alternative_is_not_picked_for_a_change() -> None:
    acc = _account()
    with session_scope() as db:
        r = ItemRepo(db)
        r.create(acc, "reminder", "Call Maria", datetime(2030, 5, 7, 6, 0, tzinfo=timezone.utc), participants="Maria")
        r.create(acc, "reminder", "Call Mario", datetime(2030, 5, 8, 6, 0, tzinfo=timezone.utc), participants="Mario")
    items = _items(acc)
    assert search(items, _q(kind="reminder", person=["Maria"]), TZ, NOW).status == "found"  # reading: fine
    res = search(items, _q(kind="reminder", person=["Maria"], purpose="change"), TZ, NOW)
    assert res.status == "ambiguous" and res.total_exact == 1 and res.total == 2  # "Mario" may be a slip: ask


def test_past_and_done_only_when_asked() -> None:
    acc = _account()
    with session_scope() as db:
        r = ItemRepo(db)
        old = r.create(acc, "reminder", "Plată chirie", NOW - timedelta(days=3))
        r.update(old, done=True)
        r.create(acc, "reminder", "Plată chirie", NOW + timedelta(days=3))
    items = _items(acc)
    res = search(items, _q(text="plata chirie"), TZ, NOW)
    assert res.status == "found" and res.candidates[0].item.due_at > NOW
    res = search(items, _q(text="plata chirie", status="done"), TZ, NOW)
    assert res.status == "found" and res.candidates[0].item.done_at is not None
    assert search(items, _q(text="plata chirie", include_past=True, status="any"), TZ, NOW).total == 2


def test_search_is_account_scoped_and_needs_criteria() -> None:
    a, b = _setup(), _account()
    assert search(_items(b), _q(text="lapte"), TZ, NOW).status == "not_found"
    assert search(_items(a), _q(), TZ, NOW).status == "insufficient"


def test_output_is_limited_but_totals_are_exact() -> None:
    acc = _account()
    with session_scope() as db:
        for i in range(8):
            ItemRepo(db).create(acc, "note", f"Lista {i}\nlapte")
    res = search(_items(acc), _q(text="lapte", limit=3), TZ, NOW)
    assert res.status == "ambiguous" and len(res.candidates) == 3 and res.total == 8 and res.truncated
