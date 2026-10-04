"""Operator API for customer accounts: list, details, create, suspend/reactivate, audit log."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app import accounts, billing, email, entitlements
from app.account_lock import lock_account
from app.api.common import device_out, hub_of
from app.db.models import Account, AuditLog, Device, Order
from app.db.repositories import DeviceRepo, UsageRepo
from app.db.session import get_session
from app.pricing.currency import convert, get_currency
from app.security import require_admin

router = APIRouter(prefix="/api/accounts", tags=["accounts"], dependencies=[Depends(require_admin)])


def _month_start() -> datetime:
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("")
def list_accounts(q: str = "", status: str | None = None, db: Session = Depends(get_session)) -> dict:
    query = select(Account).where(Account.status != "deleted")
    if status:
        query = query.where(Account.status == status)
    if q:
        like = f"%{q.lower()}%"
        query = query.where((col(Account.email).like(like)) | (col(Account.name).like(like)))
    rows = db.exec(query.order_by(col(Account.created_at).desc()).limit(500)).all()
    devices: dict[int, int] = defaultdict(int)
    for d in db.exec(select(Device).where(col(Device.token_hash).is_not(None), col(Device.account_id).is_not(None))).all():
        devices[d.account_id] += 1
    month_cost: dict[int, float] = defaultdict(float)
    for u in UsageRepo(db).since(_month_start()):
        if u.account_id is not None and u.cost_usd:
            month_cost[u.account_id] += u.cost_usd
    cur = get_currency()
    return {
        "currency": cur["currency"],
        "symbol": cur["symbol"],
        "accounts": [
            {
                **accounts.public(a),
                "last_login_at": a.last_login_at,
                "devices": devices.get(a.id, 0),
                "month_cost": convert(month_cost.get(a.id, 0.0), cur["usd_rate"]),
            }
            for a in rows
        ],
    }


def _account(db: Session, account_id: int) -> Account:
    acc = db.get(Account, account_id)
    if not acc or acc.status == "deleted":
        raise HTTPException(404, "account not found")
    return acc


@router.get("/{account_id}")
def account_detail(account_id: int, request: Request, db: Session = Depends(get_session)) -> dict:
    acc = _account(db, account_id)
    hub = hub_of(request)
    sub = billing.active_subscription(db, acc.id)
    a = entitlements.allowance(db, acc)
    return {
        **accounts.public(acc),
        "last_login_at": acc.last_login_at,
        "devices": [device_out(d, hub) for d in DeviceRepo(db).list(acc.id)],
        "subscription": sub.model_dump() if sub else None,
        "entitled": bool(sub and billing.is_entitled(sub)),
        "internal": acc.internal,
        "allowance": {
            "used": a.used,
            "limit": a.limit,
            "currency": a.currency,
            "override": acc.allowance_override,
            "used_pct": a.used_pct,
            "period_start": a.period.start,
            "period_end": a.period.end,
            "period_kind": a.period.kind,
            "unpriced_rows": a.unpriced_rows,
        },
        "orders": [o.model_dump() for o in db.exec(select(Order).where(Order.account_id == acc.id).order_by(col(Order.id).desc())).all()],
    }


class CreateBody(BaseModel):
    email: str
    name: str = Field("", max_length=120)
    country: str | None = Field(None, max_length=2)


@router.post("")
async def create_account(body: CreateBody, request: Request, db: Session = Depends(get_session)) -> dict:
    """Create an account for a customer; they receive an email to set their password."""
    try:
        acc = accounts.create(db, body.email, None, body.name, body.country)
    except accounts.AccountError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    token = accounts.issue_token(db, acc, "reset_password")
    await email.send(email.reset_password(acc.email, accounts.link("/reset-password", token)))
    accounts.audit(db, request.session["admin"], "account.create", acc.id)
    return accounts.public(acc)


class StatusBody(BaseModel):
    status: Literal["active", "suspended"]


@router.patch("/{account_id}")
async def set_status(account_id: int, body: StatusBody, request: Request, db: Session = Depends(get_session)) -> dict:
    acc = _account(db, account_id)
    lock_account(db, acc.id)  # no admission decides on this account while its status changes
    db.refresh(acc)
    acc.status = body.status
    if body.status == "suspended":
        acc.session_version += 1  # sign the customer out everywhere
    db.add(acc)
    db.commit()
    db.refresh(acc)
    hub = hub_of(request)
    for d in DeviceRepo(db).list(acc.id):
        await hub.disconnect(d.id, f"account {body.status}")  # the gateway re-checks the account on reconnect
    accounts.audit(db, request.session["admin"], f"account.{body.status}", acc.id)
    return accounts.public(acc)


@router.get("/{account_id}/audit")
def audit_log(account_id: int, db: Session = Depends(get_session)) -> list[dict]:
    rows = db.exec(
        select(AuditLog).where(AuditLog.account_id == account_id).order_by(col(AuditLog.id).desc()).limit(200)
    ).all()
    return [r.model_dump() for r in rows]
