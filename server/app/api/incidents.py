"""Operator API: ola Diagnostics incidents (admin only). Device names and ids only - no customer details."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.db.models import Device, DiagIncident, utcnow
from app.db.repositories import IncidentRepo, _aware
from app.db.session import get_session
from app.incidents.classify import CATEGORIES, CONFIDENCES, SEVERITIES
from app.incidents import texts
from app.incidents.service import detail_out, incident_out
from app.security import require_admin

router = APIRouter(prefix="/api", tags=["incidents"], dependencies=[Depends(require_admin)])


def _cursor_out(inc: DiagIncident) -> str:
    t = _aware(inc.occurred_at)
    return base64.urlsafe_b64encode(f"{t.isoformat()}|{inc.id}".encode()).decode()  # type: ignore[union-attr]


def _cursor_in(cursor: str) -> tuple[datetime, int]:
    try:
        t, i = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        when = datetime.fromisoformat(t)
        return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)), int(i)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "invalid cursor") from exc


def _choice(value: str | None, allowed: tuple[str, ...], name: str) -> str | None:
    if value and value not in allowed:
        raise HTTPException(400, f"unknown {name}")
    return value or None


def _names(db: Session, ids: set[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for i in ids:
        dev = db.get(Device, i)
        out[i] = dev.name if dev else None
    return out


@router.get("/incidents")
def list_incidents(
    category: str | None = None,
    severity: str | None = None,
    confidence: str | None = None,
    recovered: bool | None = None,
    device_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = Query(None, max_length=64),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_session),
) -> dict:
    filters = {
        "severity": _choice(severity, SEVERITIES, "severity"),
        "confidence": _choice(confidence, CONFIDENCES, "confidence"),
        "recovered": recovered,
        "device_id": device_id or None,
        "since": since,
        "until": until,
        "search": (q or "").strip() or None,
    }
    repo = IncidentRepo(db)
    rows = list(repo.page(category=_choice(category, CATEGORIES, "category"), before=_cursor_in(cursor) if cursor else None,
                          limit=limit + 1, **filters))
    more = len(rows) > limit
    rows = rows[:limit]
    names = _names(db, {r.device_id for r in rows})
    return {
        "items": [incident_out(r, names[r.device_id]) for r in rows],
        "next_cursor": _cursor_out(rows[-1]) if more and rows else None,
        "counts": repo.counts(**filters),  # per category, for the tabs (every other filter applied)
    }


@router.get("/incidents/stats")
def incident_stats(
    hours: int = Query(24, ge=1, le=168),
    device_id: str | None = None,
    db: Session = Depends(get_session),
) -> dict:
    """The dashboard: incidents per hour and category over the last `hours`, and the open problems that need
    attention (not recovered, warning or worse, last 7 days) grouped by watch and category."""
    now = utcnow()
    start = (now - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    repo = IncidentRepo(db)
    slots = [start + timedelta(hours=i) for i in range(hours + 1)]
    buckets = [{"t": t, **{c: 0 for c in CATEGORIES}} for t in slots]
    totals = {c: 0 for c in CATEGORIES}
    for t, cat in repo.timeline(start, device_id or None):
        i = int((t - start).total_seconds() // 3600)
        if 0 <= i < len(buckets) and cat in totals:
            buckets[i][cat] += 1
            totals[cat] += 1
    groups: dict[tuple[str, str], dict] = {}
    open_rows = repo.open_problems(now - timedelta(days=7), device_id or None)
    for inc in open_rows:  # newest first: the first one seen is the group's latest
        g = groups.get((inc.category, inc.device_id))
        if g is None:
            g = groups[(inc.category, inc.device_id)] = {
                "category": inc.category, "device_id": inc.device_id, "count": 0, "severity": "warn",
                "title": texts.title(inc.reason_code), "latest_id": inc.id, "latest_at": _aware(inc.occurred_at)}
        g["count"] += 1
        if inc.severity == "error":
            g["severity"] = "error"
    attention = sorted(groups.values(), key=lambda g: (g["severity"] != "error", -g["count"]))[:6]
    names = _names(db, {g["device_id"] for g in attention})
    for g in attention:
        g["device_name"] = names[g["device_id"]]
    return {"hours": hours, "buckets": buckets, "totals": totals, "attention": attention, "open_total": len(open_rows)}


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: int, db: Session = Depends(get_session)) -> dict:
    inc = db.get(DiagIncident, incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    events = list(IncidentRepo(db).events(incident_id))
    return detail_out(inc, events, _names(db, {inc.device_id})[inc.device_id])


@router.delete("/incidents")
def clear_incidents(device_id: str | None = None, db: Session = Depends(get_session)) -> dict:
    return {"deleted": IncidentRepo(db).clear(device_id)[0]}
