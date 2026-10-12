"""Global audit log (admin role only): operator actions, operator sign-ins, customer self-service actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col, func, select

from app.db.models import AuditLog
from app.db.session import get_session
from app.security import require_admin_role

router = APIRouter(prefix="/api", tags=["audit"], dependencies=[Depends(require_admin_role)])


@router.get("/audit")
def audit_log(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, max_length=64, description="action prefix, e.g. 'operator.' or 'account.delete'"),
    account_id: int | None = None,
    actor: str | None = Query(None, max_length=80),
    db: Session = Depends(get_session),
) -> dict:
    conds = []
    if action:
        conds.append(col(AuditLog.action).startswith(action, autoescape=True))
    if account_id is not None:
        conds.append(AuditLog.account_id == account_id)
    if actor:
        conds.append(AuditLog.actor == actor)
    total = db.exec(select(func.count()).select_from(AuditLog).where(*conds)).one()
    rows = db.exec(
        select(AuditLog).where(*conds).order_by(col(AuditLog.id).desc()).offset(offset).limit(limit)
    ).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "events": [
            {"id": r.id, "created_at": r.created_at, "actor": r.actor, "action": r.action,
             "account_id": r.account_id, "device_id": r.device_id, "detail": r.detail}
            for r in rows
        ],
    }
