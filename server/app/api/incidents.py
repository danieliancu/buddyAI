"""Operator API: ola Diagnostics incidents (admin only). Device names and ids only - no customer details."""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.db.models import Device, DiagIncident
from app.db.repositories import IncidentRepo, _aware
from app.db.session import get_session
from app.incidents.classify import CATEGORIES, CONFIDENCES, SEVERITIES
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
