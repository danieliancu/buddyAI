"""AI usage operations: the database decides who may spend AI money, and records what was spent.

Every AI-consuming operation (a watch turn, a customer voice sample, an operator test) is one row in
usage_operations. The database - not process memory - is the authority, so any number of server processes
(or hosts) share one budget per account:

  admit()          one short transaction: lock the account row (PostgreSQL SELECT ... FOR UPDATE; SQLite
                   BEGIN IMMEDIATE), apply the commercial rules, insert the operation with its reservation, a
                   lease and an execution token. A request id seen before never creates a second operation.
  heartbeat()      the executing process extends its lease (only with its token, only while active).
  mark_running()   before the first billable provider call.
  record_costs()   provider usage as it happens: idempotent (dedup key operation:index:kind:provider:model:unit,
                   monotonic), in integer micro-GBP, always in the operation's period - also when late.
  settle()         the end: the billable rule, the certainty of the cost, the state; idempotent.
  recover_expired() run by every process: an operation whose lease ran out (its process died or lost the
                   database) becomes "expired"; its costs stay visible to the operator but are not billed
                   (owner decision). Nothing is ever executed again.

Lock order, everywhere (also in billing.py and the admin writers): account row -> subscriptions / top-ups ->
usage_operations (ascending id) -> usage_records / usage_notices.

Admission (budget-enforced accounts; limit = plan allowance or override + top-ups of the period):
  used_final + sum(recorded of active ops) >= limit                      -> limit_reached
  other active ops and used_final + sum(max(reserved, recorded)) + own > limit -> busy_concurrent
A turn that has started is never cut off, so the last answer may go beyond its reservation: this bounds
concurrency, it is not an absolute financial cap (docs/BILLING.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, col, select

from app import allowance as allowance_mod
from app import account_lock, billing
from app.config import get_settings
from app.db.models import Account, BillingSettings, UsageOperation, UsageRecord
from app.db.session import OWNER, billing_scope, db_now, is_postgres, session_scope
from app.money import pence_to_micro
from app.plan import get_plan
from app.pricing.pricing import ProviderPricingConfig
from app.providers.base import UsageItem

log = logging.getLogger("buddyai.usage")

ACTIVE = ("reserved", "running")
TERMINAL = ("settled", "cancelled", "expired")

# Refusal codes (protocol error codes). Legacy watches get "busy" for the ones they do not know.
LIMIT_REACHED = "limit_reached"
BUSY_CONCURRENT = "busy_concurrent"
SUBSCRIPTION_REQUIRED = "subscription_required"
ACCOUNT_INACTIVE = "account_inactive"
SERVICE_UNAVAILABLE = "service_unavailable"
REQUEST_CONFLICT = "request_conflict"
DUPLICATE = "duplicate"
NEW_CODES = (BUSY_CONCURRENT, SERVICE_UNAVAILABLE, REQUEST_CONFLICT, DUPLICATE)

MESSAGES = {
    LIMIT_REACHED: "allowance for this period used up",
    BUSY_CONCURRENT: "other conversations of this account are in progress; try again when they finish",
    SUBSCRIPTION_REQUIRED: "ola Care subscription needed",
    ACCOUNT_INACTIVE: "account suspended or closed",
    SERVICE_UNAVAILABLE: "Ola can't answer right now. Please try again a little later.",
    REQUEST_CONFLICT: "request id already used for a different request",
    DUPLICATE: "this request is already running",
}

# --- test hooks: named pause / fault points (no-ops unless a test installs a callable) ----------------
HOOKS: dict[str, Callable[..., None]] = {}


def _hook(name: str, **ctx: Any) -> None:
    fn = HOOKS.get(name)
    if fn is not None:
        fn(**ctx)


# Separate pools: admissions/settlements may wait for an account lock; leases and costs must not wait
# behind them (a starved heartbeat would let a healthy turn expire).
ADMIT_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="usage-admit")
LEASE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="usage-lease")


async def in_pool(pool: ThreadPoolExecutor, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.get_running_loop().run_in_executor(pool, lambda: fn(*args, **kwargs))


# --- requests and results -------------------------------------------------------------------------------


def fingerprint(*parts: Any) -> str:
    return hashlib.sha256("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()


def new_token() -> str:
    return uuid.uuid4().hex


@dataclass
class AdmitRequest:
    device_id: str
    request_key: str  # r:<request id> | l:<device>:<session>:<turn> | s:<uuid>
    request_kind: str  # client | legacy | server
    kind: str  # chat | note | reminder | voice_sample | operator_test
    account_id: int | None
    fingerprint: str
    exec_token: str = field(default_factory=new_token)  # ours: a retried admit after a lost reply finds it
    budget: bool = True  # False: recorded but never budget-checked (free voice sample)


@dataclass
class Admission:
    allowed: bool
    code: str | None = None
    message: str = ""
    op_id: int | None = None
    op_uid: str | None = None
    exec_token: str | None = None
    duplicate: str | None = None  # None | "active" | "finished"
    finished_status: str | None = None
    lease_s: int = 0

    @classmethod
    def refuse(cls, code: str, **kw: Any) -> "Admission":
        return cls(False, code, MESSAGES.get(code, code), **kw)


@dataclass
class OpView:
    id: int
    op_uid: str
    state: str
    cost_certainty: str
    reserved_micro: int
    recorded_cost_micro: int
    billable: bool | None
    result_status: str | None
    reason: str | None

    @classmethod
    def of(cls, op: UsageOperation) -> "OpView":
        return cls(op.id or 0, op.op_uid, op.state, op.cost_certainty, op.reserved_micro, op.recorded_cost_micro,
                   op.billable, op.result_status, op.reason)


# --- transaction helpers ------------------------------------------------------------------------------


def _locked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "database is locked" in msg or "lock timeout" in msg or "could not obtain lock" in msg or "deadlock" in msg


def _run(fn: Callable[[Session], Any], what: str) -> Any:
    """Run fn in one billing transaction; retries a bounded number of times when the lock is busy."""
    s = get_settings()
    attempts = max(1, s.usage_admit_retries)
    for attempt in range(attempts):
        try:
            with billing_scope() as db:
                if is_postgres():
                    ms = int(s.usage_lock_timeout_ms)
                    db.exec(text(f"SET LOCAL lock_timeout = '{ms}ms'"))  # type: ignore[call-overload]
                    db.exec(text(f"SET LOCAL statement_timeout = '{ms * 3}ms'"))  # type: ignore[call-overload]
                out = fn(db)
                db.commit()
                return out
        except OperationalError as exc:
            if attempt + 1 < attempts and _locked(exc):
                time.sleep(0.02 * (2 ** attempt))
                continue
            raise
    raise RuntimeError(f"{what}: retries exhausted")  # pragma: no cover


def lock_account(db: Session, account_id: int, skip_locked: bool = False) -> Account | None:
    """The account row, locked for this transaction (PostgreSQL). SQLite: the transaction already holds the
    database write lock (BEGIN IMMEDIATE)."""
    return account_lock.lock_account(db, account_id, skip_locked)


def _lock_op(db: Session, op_id: int, skip_locked: bool = False) -> UsageOperation | None:
    q = select(UsageOperation).where(UsageOperation.id == op_id)
    if is_postgres():
        q = q.with_for_update(skip_locked=skip_locked)
    return db.exec(q).first()


def _aware(dt: datetime | None) -> datetime | None:
    return allowance_mod._aware(dt)


def _log(event: str, op: UsageOperation | None = None, **kw: Any) -> None:
    fields = {"event": event, "owner": OWNER}
    if op is not None:
        fields.update(op=op.op_uid, account=op.account_id, device=op.device_id, state=op.state,
                      reserved=op.reserved_micro, recorded=op.recorded_cost_micro, billable=op.billable,
                      certainty=op.cost_certainty)
    fields.update(kw)
    log.info("usage %s", " ".join(f"{k}={v}" for k, v in fields.items()))


# --- admission ------------------------------------------------------------------------------------------


def _find(db: Session, account_id: int | None, device_id: str, request_key: str) -> UsageOperation | None:
    q = select(UsageOperation).where(UsageOperation.request_key == request_key)
    q = q.where(UsageOperation.account_id == account_id) if account_id is not None else q.where(
        col(UsageOperation.account_id).is_(None), UsageOperation.device_id == device_id)
    return db.exec(q).first()


def _existing(req: AdmitRequest, op: UsageOperation) -> Admission:
    if op.request_fingerprint != req.fingerprint:
        return Admission.refuse(REQUEST_CONFLICT)
    if op.exec_token == req.exec_token:  # our own admit, retried after its reply was lost: it did commit
        return Admission(True, op_id=op.id, op_uid=op.op_uid, exec_token=op.exec_token,
                         lease_s=get_settings().usage_lease_s)
    if op.state in ACTIVE:
        return Admission.refuse(DUPLICATE, duplicate="active", op_id=op.id, op_uid=op.op_uid)
    return Admission.refuse(DUPLICATE, duplicate="finished", op_id=op.id, op_uid=op.op_uid,
                            finished_status=op.result_status or op.state)


def _rules(db: Session, acc: Account | None, now: datetime, budget: bool = True, own: bool = True,
           ) -> tuple[str | None, allowance_mod.PeriodId, str, int]:
    """(refusal code or None, period, source, reservation) for a new operation of this account."""
    plan = get_plan(db)
    sub = billing.active_subscription(db, acc.id) if acc is not None else None  # type: ignore[arg-type]
    pid = allowance_mod.period_identity(sub, now)
    if acc is None:
        return None, pid, "unowned", 0
    if acc.internal:
        return None, pid, "internal", 0
    if not budget or not plan.enforced:
        return None, pid, "unenforced", 0
    if sub is None or not billing.is_entitled(sub, now):
        return SUBSCRIPTION_REQUIRED, pid, pid.source, 0
    used, _unpriced = allowance_mod.used_final(db, acc.id, pid)  # type: ignore[arg-type]
    n_active, recorded, exposure = allowance_mod.active_exposure(db, acc.id, pid.key)  # type: ignore[arg-type]
    limit = allowance_mod.included_micro(acc, sub, plan) + allowance_mod.topups_micro(db, acc.id, now, pid.key)  # type: ignore[arg-type]
    reserve = pence_to_micro(plan.reserve_pence) if own else 0
    if used + recorded >= limit:
        _log("refused", account=acc.id, code=LIMIT_REACHED, used=used, recorded=recorded, limit=limit)
        return LIMIT_REACHED, pid, pid.source, 0
    if own and n_active and used + exposure + reserve > limit:
        _log("refused", account=acc.id, code=BUSY_CONCURRENT, used=used, exposure=exposure, limit=limit,
             active=n_active)
        return BUSY_CONCURRENT, pid, pid.source, 0
    return None, pid, pid.source, reserve


def preview(account_id: int | None) -> Admission:
    """Would a single new operation of this account be admitted now? Read-only (no reservation): for
    screens and tools, never for spending."""
    if account_id is None:
        return Admission(True)
    with session_scope() as db:  # a plain read: no account lock, no write lock
        acc = db.get(Account, account_id)
        if acc is None or acc.status != "active":
            return Admission.refuse(ACCOUNT_INACTIVE)
        code, *_ = _rules(db, acc, db_now(db), own=False)
        db.rollback()
    return Admission.refuse(code) if code else Admission(True)


def _admit_tx(db: Session, req: AdmitRequest) -> Admission:
    s = get_settings()
    acc = lock_account(db, req.account_id) if req.account_id is not None else None
    _hook("admit_locked", req=req)
    if req.account_id is not None and acc is None:
        return Admission.refuse(ACCOUNT_INACTIVE)
    existing = _find(db, req.account_id, req.device_id, req.request_key)
    if existing is not None:
        return _existing(req, existing)
    settings_row = db.get(BillingSettings, 1)
    if settings_row is not None and settings_row.admission_paused:
        return Admission.refuse(SERVICE_UNAVAILABLE)
    if acc is not None and acc.status != "active":
        return Admission.refuse(ACCOUNT_INACTIVE)
    now = db_now(db)
    code, pid, source, reserve = _rules(db, acc, now, budget=req.budget)
    if code is not None:
        return Admission.refuse(code)
    lease_s = int(s.usage_lease_s)
    op = UsageOperation(
        op_uid=uuid.uuid4().hex, account_id=req.account_id, device_id=req.device_id,
        request_key=req.request_key, request_kind=req.request_kind, request_fingerprint=req.fingerprint,
        kind=req.kind, period_key=pid.key, period_kind=pid.period.kind, period_start=pid.period.start,
        period_end=pid.period.end, source=source, subscription_id=pid.subscription_id,
        enforced=source in ("stripe", "complimentary", "calendar"), state="reserved", reserved_micro=reserve,
        owner=OWNER, exec_token=req.exec_token, accepted_at=now, heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=lease_s), created_at=now, updated_at=now,
    )
    db.add(op)
    db.flush()
    _hook("admit_before_commit", req=req, op_id=op.id)
    _log("admitted", op, kind=req.kind, request=req.request_kind)
    return Admission(True, op_id=op.id, op_uid=op.op_uid, exec_token=op.exec_token, lease_s=lease_s)


def admit(req: AdmitRequest) -> Admission:
    """Admit (or refuse) one operation. Never allows when the database cannot decide."""
    try:
        out = _run(lambda db: _admit_tx(db, req), "admit")
        _hook("admit_after_commit", req=req, admission=out)
        return out
    except IntegrityError:
        # The same request committed concurrently by another process: report that one.
        try:
            def again(db: Session) -> Admission:
                op = _find(db, req.account_id, req.device_id, req.request_key)
                return _existing(req, op) if op is not None else Admission.refuse(SERVICE_UNAVAILABLE)
            return _run(again, "admit-retry")
        except Exception:  # noqa: BLE001
            log.exception("usage admit retry failed")
            return Admission.refuse(SERVICE_UNAVAILABLE)
    except Exception:  # noqa: BLE001 - database down / timeout: refuse, never allow
        log.warning("usage admit failed: database unavailable", exc_info=True)
        return Admission.refuse(SERVICE_UNAVAILABLE)


# --- lease ----------------------------------------------------------------------------------------------


def _extend(op_id: int, token: str, running: bool) -> bool:
    def tx(db: Session) -> bool:
        now = db_now(db)
        values: dict[str, Any] = {
            "lease_expires_at": now + timedelta(seconds=int(get_settings().usage_lease_s)),
            "heartbeat_at": now, "updated_at": now,
        }
        if running:
            values["state"] = "running"
        res = db.exec(  # type: ignore[call-overload]
            update(UsageOperation)
            .where(UsageOperation.id == op_id, UsageOperation.exec_token == token,
                   col(UsageOperation.state).in_(ACTIVE), col(UsageOperation.lease_expires_at) > now)
            .values(**values)
        )
        return res.rowcount == 1

    return bool(_run(tx, "heartbeat"))


def heartbeat(op_id: int, token: str) -> bool:
    """Extend the lease. False = the lease is lost (expired / settled elsewhere): stop paid work."""
    _hook("heartbeat", op_id=op_id)
    return _extend(op_id, token, running=False)


def mark_running(op_id: int, token: str) -> bool:
    """reserved -> running before the first billable provider call (also extends the lease)."""
    return _extend(op_id, token, running=True)


# --- costs ----------------------------------------------------------------------------------------------


def dedup_key(op_uid: str, index: int, item: UsageItem) -> str:
    return f"{op_uid}:{index}:{item.kind}:{item.provider}:{item.model}:{item.unit}"[:160]


def _reconcile(db: Session, op: UsageOperation, items: Iterable[tuple[int, UsageItem]], now: datetime,
               turn_db_id: int | None = None) -> int:
    """Write the items as usage records of the operation (once per dedup key; a larger quantity for the
    same key replaces the smaller one - live counters only grow). Returns the number of new rows."""
    pricing = ProviderPricingConfig.load(db)
    items = [(i, it) for i, it in items if it.quantity]
    if not items:
        return 0
    keys = {dedup_key(op.op_uid, i, it): (i, it) for i, it in items}
    existing = {r.dedup_key: r for r in db.exec(
        select(UsageRecord).where(UsageRecord.operation_id == op.id, col(UsageRecord.dedup_key).in_(list(keys)))
    ).all()}
    billable = op.billable if op.state in TERMINAL and op.billable is not None else True
    added = 0
    for key, (_i, it) in keys.items():
        row = existing.get(key)
        if row is not None:
            if it.quantity > (row.quantity or 0):
                fresh = pricing.records([it], op.device_id, row.turn_id, op.account_id)[0]
                row.quantity, row.cost_usd, row.cost_micro_gbp, row.fx_rate = (
                    fresh.quantity, fresh.cost_usd, fresh.cost_micro_gbp, fresh.fx_rate)
                db.add(row)
            else:
                _log("dedup_skip", op, key=key.split(":", 1)[1])
            continue
        rec = pricing.records([it], op.device_id, turn_db_id, op.account_id, billable=billable)[0]
        rec.operation_id, rec.dedup_key, rec.period_key, rec.stage = op.id, key, op.period_key, it.kind
        db.add(rec)
        added += 1
    db.flush()
    _refresh_totals(db, op, now)
    return added


def _refresh_totals(db: Session, op: UsageOperation, now: datetime) -> None:
    rows = db.exec(select(UsageRecord).where(UsageRecord.operation_id == op.id, UsageRecord.mock == False)).all()  # noqa: E712
    op.recorded_cost_micro = sum(r.cost_micro_gbp or 0 for r in rows)
    op.unpriced_count = sum(1 for r in rows if r.cost_micro_gbp is None)
    if op.state in TERMINAL:
        op.billable_cost_micro = op.recorded_cost_micro if op.billable else 0
    op.last_cost_at = op.updated_at = now
    db.add(op)


def record_costs(op_id: int, items: Iterable[tuple[int, UsageItem]], turn_db_id: int | None = None) -> int:
    """Record provider usage of an operation as it happens (idempotent). A cost arriving after the operation
    ended stays in the operation's period, with the operation's billable flag (logged as late)."""
    items = list(items)

    def tx(db: Session) -> int:
        op0 = db.get(UsageOperation, op_id)
        if op0 is None:
            return 0
        if op0.account_id is not None:
            lock_account(db, op0.account_id)
        op = _lock_op(db, op_id)
        assert op is not None
        late = op.state in TERMINAL
        added = _reconcile(db, op, items, db_now(db), turn_db_id)
        if late and added:
            _log("late_cost", op, rows=added)
        return added

    return int(_run(tx, "record_costs"))


# --- settlement -----------------------------------------------------------------------------------------


def settle(op_id: int, token: str, *, status: str, billable: bool, items: Iterable[tuple[int, UsageItem]] = (),
           turn_db_id: int | None = None, reason: str | None = None) -> OpView | None:
    """End the operation: reconcile the last costs, apply the billable decision to all its records, set the
    state (settled, or cancelled when nothing was spent) and the cost certainty. Idempotent; if recovery
    already expired it, the costs are added as late costs and the expired result stands."""
    items = list(items)

    def tx(db: Session) -> OpView | None:
        op0 = db.get(UsageOperation, op_id)
        if op0 is None:
            return None
        if op0.account_id is not None:
            lock_account(db, op0.account_id)
        op = _lock_op(db, op_id)
        assert op is not None
        now = db_now(db)
        _hook("settle_locked", op_id=op_id)
        if op.state in TERMINAL:
            added = _reconcile(db, op, items, now, turn_db_id)
            if added:
                _log("late_cost", op, rows=added, via="settle")
            _link_turn(db, op, turn_db_id, status)
            return OpView.of(op)
        if op.exec_token != token:  # never another process's operation
            _log("settle_refused", op, why="token")
            return OpView.of(op)
        _reconcile(db, op, items, now, turn_db_id)
        rows = db.exec(select(UsageRecord).where(UsageRecord.operation_id == op.id)).all()
        for r in rows:
            r.billable = billable
            r.turn_status = status
            db.add(r)
        op.billable = billable
        op.state = "settled" if rows else "cancelled"
        op.cost_certainty = "unpriced" if op.unpriced_count else "exact"
        op.result_status, op.reason = status, reason
        op.finished_at = op.updated_at = now
        op.lease_expires_at = None
        _refresh_totals(db, op, now)
        _link_turn(db, op, turn_db_id, status)
        _log("settled", op, status=status, reason=reason)
        return OpView.of(op)

    return _run(tx, "settle")


def _link_turn(db: Session, op: UsageOperation, turn_db_id: int | None, status: str | None) -> None:
    if turn_db_id is None:
        return
    op.turn_db_id = turn_db_id
    db.add(op)
    for r in db.exec(select(UsageRecord).where(UsageRecord.operation_id == op.id, col(UsageRecord.turn_id).is_(None))).all():
        r.turn_id, r.turn_uid = turn_db_id, f"t{turn_db_id}"
        r.turn_status = r.turn_status or status
        db.add(r)


# --- recovery -------------------------------------------------------------------------------------------


def recover_expired(limit: int = 50) -> int:
    """Expire active operations whose lease ran out (any process may run this; each operation is taken by
    one). Their costs are kept (visible to the operator) and not billed; certainty is exact when nothing
    can have been spent, otherwise uncertain (a provider may still report usage: it lands as a late cost)."""
    try:
        def scan(db: Session) -> list[tuple[int, int | None]]:
            now = db_now(db)
            return [(r.id, r.account_id) for r in db.exec(  # type: ignore[misc]
                select(UsageOperation).where(col(UsageOperation.state).in_(ACTIVE),
                                             col(UsageOperation.lease_expires_at) < now)
                .order_by(col(UsageOperation.id)).limit(limit)).all()]

        candidates = _run(scan, "recover-scan")
    except Exception:  # noqa: BLE001
        log.warning("usage recovery scan failed", exc_info=True)
        return 0
    done = 0
    for op_id, account_id in candidates:
        try:
            if _run(lambda db, i=op_id, a=account_id: _expire_tx(db, i, a), "recover"):
                done += 1
        except Exception:  # noqa: BLE001
            log.warning("usage recovery of op %s failed", op_id, exc_info=True)
    return done


def _expire_tx(db: Session, op_id: int, account_id: int | None) -> bool:
    if account_id is not None and lock_account(db, account_id, skip_locked=True) is None:
        return False  # another process holds the account: it (or a later pass) will do it
    op = _lock_op(db, op_id, skip_locked=True)
    if op is None:
        return False
    now = db_now(db)
    if op.state not in ACTIVE or _aware(op.lease_expires_at) is None or _aware(op.lease_expires_at) >= now:  # type: ignore[operator]
        return False  # settled or extended meanwhile
    _hook("recover_locked", op_id=op_id)
    nothing_spent = op.state == "reserved" and op.recorded_cost_micro == 0 and not op.unpriced_count
    for r in db.exec(select(UsageRecord).where(UsageRecord.operation_id == op.id)).all():
        r.billable = False
        db.add(r)
    op.state, op.billable, op.reason = "expired", False, "lease_expired"
    op.cost_certainty = "exact" if nothing_spent else "uncertain"
    op.result_status = op.result_status or "expired"
    op.finished_at = op.updated_at = now
    _refresh_totals(db, op, now)
    _log("expired", op)
    return True


# --- settlements that could not be written (database briefly unavailable) ---------------------------------

_PENDING: list[dict[str, Any]] = []  # process-local retry queue (lost on a crash: recovery expires those ops)


def queue_settle(op_id: int, token: str, **kw: Any) -> None:
    _PENDING.append({"op_id": op_id, "token": token, **kw})


def flush_pending() -> int:
    done = 0
    for job in list(_PENDING):
        try:
            settle(job["op_id"], job["token"], **{k: v for k, v in job.items() if k not in ("op_id", "token")})
            _PENDING.remove(job)
            done += 1
        except Exception:  # noqa: BLE001
            log.warning("usage: queued settle of op %s still failing", job["op_id"])
            break
    return done


def pending_count() -> int:
    return len(_PENDING)


async def maintenance_loop() -> None:
    """Every process: retry queued settlements, then expire operations whose lease ran out."""
    interval = max(1.0, float(get_settings().usage_recovery_interval_s))
    while True:
        try:
            await in_pool(ADMIT_POOL, flush_pending)
            n = await in_pool(ADMIT_POOL, recover_expired)
            if n:
                log.info("usage: %s expired operation(s) recovered", n)
        except Exception:  # noqa: BLE001
            log.warning("usage maintenance failed", exc_info=True)
        await asyncio.sleep(interval)


def database_ready() -> bool:
    try:
        with billing_scope() as db:
            db.exec(text("SELECT 1"))  # type: ignore[call-overload]
            db.rollback()
        return True
    except Exception:  # noqa: BLE001
        return False


# --- operations outside watch turns -----------------------------------------------------------------------


def record_free_operation(kind: str, device_id: str, account_id: int | None, items: list[UsageItem],
                          status: str = "completed") -> OpView | None:
    """A non-billable operation recorded after the fact: customer voice samples (free, rate-limited) and
    operator tests (documented exception: they run on operator request, outside any customer budget)."""
    try:
        req = AdmitRequest(device_id=device_id or "-", request_key=f"s:{uuid.uuid4().hex}", request_kind="server",
                           kind=kind, account_id=None if kind == "operator_test" else account_id,
                           fingerprint=fingerprint(device_id, kind), budget=False)
        a = admit(req)
        if not a.allowed or a.op_id is None:
            log.warning("usage: %s not recorded (%s)", kind, a.code)
            return None
        return settle(a.op_id, a.exec_token or "", status=status, billable=False,
                      items=list(enumerate(items)), reason=kind)
    except Exception:  # noqa: BLE001
        log.warning("usage: recording %s failed", kind, exc_info=True)
        return None

