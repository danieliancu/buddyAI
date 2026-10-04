"""Finding notes and reminders from what the user says (no numbers needed).

All of the account's items of the asked kind are scored in Python (an account has at most 100 per kind),
so "exactly one match" is a fact about the whole list, not about a truncated page. Matching ignores case,
spacing and diacritics and tolerates small transcription differences; conditions the user stated
explicitly (a day, a time, a person, a place) are hard filters. A match that only holds through a fuzzy
(misspelled) word is never `exact`: it can be proposed to the user, not acted on.

Statuses: found (exactly one exact match - and, for a change / delete / copy, no other plausible match
at all), ambiguous (several matches, or only fuzzy ones), not_found, insufficient (nothing to search by).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from difflib import SequenceMatcher
from typing import Any
from zoneinfo import ZoneInfo

from app.db.models import Item
from app.notes_edit import note_lines

EXACT, PREFIX, FUZZY = 3, 2, 1
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_LIMIT = 10
STATUSES = ("open", "done", "overdue", "any")  # "" = not said: open ones (completed only when asked)
# Words that carry no meaning for finding an item (ro / en, without diacritics).
STOPWORDS = {
    "de", "la", "cu", "si", "pe", "din", "in", "un", "o", "a", "al", "ale", "lui", "mea", "meu", "mele", "mei",
    "the", "an", "of", "to", "my", "with", "for", "on", "at", "and",
    "nota", "notita", "notite", "note", "notes", "lista", "list", "reminder", "reminderul", "memento",
    "programare", "programarea", "intalnire", "intalnirea",
}
_FOLD = str.maketrans({"ß": "ss", "ø": "o", "ł": "l", "đ": "d", "æ": "ae", "œ": "oe", "ı": "i", "ð": "d", "þ": "th"})


def normalize(text: str) -> str:
    """Lower case, no diacritics, punctuation as spaces, single spaces ('Ștefan,  BIO' -> 'stefan bio')."""
    t = unicodedata.normalize("NFKD", (text or "").casefold().translate(_FOLD))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w]+", " ", t)
    return " ".join(t.split())


def tokens(text: str) -> list[str]:
    return [w for w in normalize(text).split() if w not in STOPWORDS]


def _damerau(a: str, b: str, limit: int) -> int:
    """Edit distance with transpositions, stopping early above `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def word_match(q: str, w: str) -> int:
    """How well a query word matches a word of the item: EXACT, PREFIX, FUZZY or 0."""
    if q == w:
        return EXACT
    if len(q) >= 4 and len(w) >= 4 and (w.startswith(q) or q.startswith(w)):
        return PREFIX
    if len(q) >= 4 and len(w) >= 4:
        limit = 1 if max(len(q), len(w)) < 8 else 2
        if _damerau(q, w, limit) <= limit or SequenceMatcher(None, q, w).ratio() >= 0.85:
            return FUZZY
    return 0


def best_match(q: str, words: list[str]) -> int:
    return max((word_match(q, w) for w in words), default=0)


@dataclass
class ItemQuery:
    kind: str = "any"  # note | reminder | any
    text: str = ""  # words that appear in the item (any line of a note, a reminder's text, place, people)
    person: list[str] = field(default_factory=list)
    place: str = ""
    date: str = ""  # YYYY-MM-DD (local)
    date_from: str = ""
    date_to: str = ""
    weekday: str = ""  # mon..sun
    time: str = ""  # HH:MM (local start, or inside the range)
    status: str = ""  # open | done | overdue | any; "" = not said
    include_past: bool = False
    number: int | None = None  # the number shown on the watch, when the user says it
    purpose: str = "read"  # read | change | delete | duplicate
    limit: int = 5

    @classmethod
    def from_args(cls, a: dict[str, Any]) -> "ItemQuery":
        person = a.get("person") or []
        if isinstance(person, str):
            person = [p for p in person.split(",")]
        weekday = str(a.get("weekday") or "")[:3].lower()
        number = a.get("number")
        return cls(
            kind=a.get("kind") if a.get("kind") in ("note", "reminder") else "any",
            text=str(a.get("text") or "")[:200],
            person=[str(p).strip() for p in person if str(p).strip()][:5],
            place=str(a.get("place") or "")[:120],
            date=str(a.get("date") or "")[:10],
            date_from=str(a.get("date_from") or "")[:10],
            date_to=str(a.get("date_to") or "")[:10],
            weekday=weekday if weekday in WEEKDAYS else "",
            time=str(a.get("time") or "")[:5],
            status=a.get("status") if a.get("status") in STATUSES else "",
            include_past=a.get("include_past") is True,
            number=int(number) if isinstance(number, (int, float)) and not isinstance(number, bool) else None,
            purpose=a.get("purpose") if a.get("purpose") in ("read", "change", "delete", "duplicate") else "read",
            limit=max(1, min(MAX_LIMIT, int(a.get("limit") or 5))),
        )

    def has_criteria(self) -> bool:
        return bool(
            tokens(self.text) or self.person or self.place.strip() or self.date or self.date_from or self.date_to
            or self.weekday or self.time or self.number is not None or self.status in ("done", "overdue")
        )

    def names_a_past_day(self, today: date) -> bool:
        days = [d for d in (self.date, self.date_to or self.date_from) if d]
        return any(_date(d) is not None and _date(d) < today for d in days)  # type: ignore[operator]


@dataclass
class Candidate:
    item: Item
    exact: bool
    score: float
    reasons: list[str]
    line: int | None = None  # notes: the line where the text was found (0 = title, n = numbered line n)


@dataclass
class SearchResult:
    status: str  # found | ambiguous | not_found | insufficient
    candidates: list[Candidate]  # the ones shown (exact ones first), at most query.limit
    total: int  # every match, exact or fuzzy
    total_exact: int
    truncated: bool
    differences: list[str]  # which fields tell the shown candidates apart (day, time, place, people, text)


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _hhmm(value: str) -> time | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return time(int(m.group(1)), int(m.group(2)))


def _field_hit(query: str, words: list[str]) -> int:
    """All words of `query` found in `words`: the weakest tier among them (0 if one is missing)."""
    q = tokens(query)
    if not q:
        return 0
    return min(best_match(w, words) for w in q)


def _in_scope(it: Item, q: ItemQuery, zone: ZoneInfo, now: datetime) -> bool:
    """Which reminders a query looks at. Completed ones only when asked (status done / any, or the past);
    days before today only when asked - except open overdue ones, which can still be changed, deleted or
    copied."""
    if it.kind == "note":
        return True
    done = it.done_at is not None
    if q.status == "done":
        return done
    if q.status == "open" and done:
        return False
    if done and q.status != "any" and not q.include_past:
        return False
    today = now.astimezone(zone).date()
    due_day = it.due_at.astimezone(zone).date() if it.due_at else today
    if due_day >= today or q.include_past or q.names_a_past_day(today):
        return True
    return q.purpose != "read" and not done


def search(items: list[Item], q: ItemQuery, tz: str, now: datetime | None = None) -> SearchResult:
    """Score the account's items (already filtered by account) against the query."""
    if not q.has_criteria():
        return SearchResult("insufficient", [], 0, 0, False, [])
    zone = ZoneInfo(tz)
    now = now or datetime.now(timezone.utc)
    want_date, d_from, d_to = _date(q.date), _date(q.date_from), _date(q.date_to)
    want_time = _hhmm(q.time) if q.time else None
    out: list[Candidate] = []
    for it in items:
        if q.kind != "any" and it.kind != q.kind:
            continue
        if q.number is not None and it.number != q.number:
            continue
        if not _in_scope(it, q, zone, now):
            continue
        reasons: list[str] = []
        exact = True
        score = 0.0
        line_hit: int | None = None
        if it.kind == "reminder":
            local = it.due_at.astimezone(zone) if it.due_at else None
            end = it.end_at.astimezone(zone) if it.end_at else None
            if want_date or d_from or d_to or q.weekday or want_time:
                if local is None:
                    continue
                day = local.date()
                if want_date and day != want_date:
                    continue
                if d_from and day < d_from or d_to and day > d_to:
                    continue
                if q.weekday and WEEKDAYS[day.weekday()] != q.weekday:
                    continue
                if want_time:
                    start_t = local.time().replace(second=0, microsecond=0)
                    inside = end is not None and start_t <= want_time <= end.time()
                    if start_t != want_time and not inside:
                        continue
                reasons.append(f"{WEEKDAYS[day.weekday()]} {local:%Y-%m-%d %H:%M}")
                score += 2
            if q.status == "overdue" and not (it.done_at is None and it.due_at and it.due_at <= now):
                continue
            if q.status == "open" and it.done_at is not None:
                continue
        elif any((want_date, d_from, d_to, q.weekday, want_time)) or q.status in ("done", "overdue"):
            continue  # notes have no date or state
        if q.status == "done" and it.kind == "reminder" and it.done_at is not None:
            reasons.append("completed")
        # people and place: hard filters; an exact or prefix word match is needed to be exact
        people_words = normalize(it.participants or "").split()
        text_words = normalize(it.text).split()
        place_words = normalize(it.location or "").split()
        hits = []
        for p in q.person:
            hits.append((max(_field_hit(p, people_words), _field_hit(p, text_words)), f"person: {p}"))
        if q.place.strip():
            hits.append((max(_field_hit(q.place, place_words), _field_hit(q.place, text_words)), f"place: {q.place}"))
        if tokens(q.text):  # words like "note" / "meeting" alone say nothing about which one
            hits.append((_field_hit(q.text, text_words + place_words + people_words) * len(tokens(q.text)), "text"))
        if any(h == 0 for h, _ in hits):
            continue
        for h, why in hits:
            exact &= (h if why != "text" else h // max(1, len(tokens(q.text)))) >= PREFIX
            score += h
            if why != "text" or it.kind == "reminder":
                reasons.append(why)
        if tokens(q.text) and it.kind == "note":
            for idx, ln in enumerate(note_lines(it.text)):
                if _field_hit(q.text, normalize(ln).split()):
                    line_hit = idx
                    reasons.append(f"text: line {idx}" if idx else "text: title")
                    break
        if q.number is not None:
            reasons.append(f"number {q.number}")
        out.append(Candidate(it, exact, score, reasons, line_hit))
    exact_ones = [c for c in out if c.exact]
    out.sort(key=lambda c: (not c.exact, -c.score, _proximity(c.item, now)))
    shown = out[: q.limit]
    # Reading: one exact match is enough. Changing / deleting / copying: also no plausible alternative
    # (a near-identical name may be a transcription error - ask instead of touching the wrong one).
    if len(exact_ones) == 1 and (q.purpose == "read" or len(out) == 1):
        status = "found"
        shown = [exact_ones[0]]
    elif exact_ones or out:
        status = "ambiguous"
    else:
        status = "not_found"
    return SearchResult(status, shown, len(out), len(exact_ones), len(out) > len(shown), _differences(shown, zone))


def _proximity(it: Item, now: datetime) -> float:
    if it.due_at is None:
        return 0.0
    return abs((it.due_at - now).total_seconds())


def _differences(cands: list[Candidate], zone: ZoneInfo) -> list[str]:
    if len(cands) < 2:
        return []
    out = []
    rem = [c.item for c in cands if c.item.kind == "reminder" and c.item.due_at]
    if len({it.due_at.astimezone(zone).date() for it in rem}) > 1:
        out.append("day")
    if len({it.due_at.astimezone(zone).strftime("%H:%M") for it in rem}) > 1:
        out.append("time")
    if len({(c.item.location or "") for c in cands}) > 1:
        out.append("place")
    if len({(c.item.participants or "") for c in cands}) > 1:
        out.append("people")
    if len({normalize(c.item.text)[:40] for c in cands}) > 1:
        out.append("text")
    if len({c.item.kind for c in cands}) > 1:
        out.append("kind")
    return out
