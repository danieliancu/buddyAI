"""Operator API: the flat list of diagnostic events (older clients). ola Diagnostics uses /api/incidents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.db.models import Device
from app.db.repositories import IncidentRepo, IssueRepo
from app.db.session import get_session
from app.issues import issue_out
from app.security import require_admin

router = APIRouter(prefix="/api", tags=["issues"], dependencies=[Depends(require_admin)])


@router.get("/issues")
def list_issues(
    device_id: str | None = None,
    kind: str | None = None,
    problems_only: bool = False,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> list[dict]:
    rows = IssueRepo(db).list(device_id, kind, problems_only, limit)
    names: dict[str, str | None] = {}
    for r in rows:
        if r.device_id not in names:
            dev = db.get(Device, r.device_id)
            names[r.device_id] = dev.name if dev else None
    return [issue_out(r, names[r.device_id]) for r in rows]


@router.delete("/issues")
def clear_issues(device_id: str | None = None, db: Session = Depends(get_session)) -> dict:
    return {"deleted": IncidentRepo(db).clear(device_id)[1]}  # the events and the incidents they belong to
