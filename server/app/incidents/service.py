"""ola Diagnostics: storing events, grouping them into incidents, recovery, retention, live updates.

Diagnostics must never become a failure of their own: writes run off the event loop, every error is caught
and logged, and events that could not be stored (database unavailable) wait in a bounded in-memory queue
that the maintenance loop retries. Storing is idempotent (dedup keys), so a retry never duplicates.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from datetime import timedelta
from typing import Any, Iterable

from app.config import get_settings
from app.db.models import Device, DeviceIssue, DiagIncident, utcnow
from app.db.repositories import IncidentRepo, _aware
from app.db.session import session_scope
from sqlmodel import select
from app.incidents import texts
from app.incidents.classify import classify_event, classify_incident
from app.incidents.events import Event

log = logging.getLogger(__name__)

RETRY_MAX = 500
RETRY_INTERVAL_S = 30.0
PRUNE_INTERVAL_S = 3600.0

# (function name, args): "record" (events), "session_started" / "turn_succeeded" (recovery marks)
_retry: deque[tuple[str, tuple]] = deque(maxlen=RETRY_MAX)
_retry_lock = threading.Lock()
_tasks: set[asyncio.Task] = set()
stats = {"stored": 0, "duplicates": 0, "queued": 0, "dropped": 0, "failed": 0}


# --- storing ---------------------------------------------------------------------------------------------


def record(device_id: str, account_id: int | None, fw: str, events: Iterable[Event]) -> list[int]:
    """Store events and regroup their incidents. Returns the ids of the incidents that changed."""
    changed: list[int] = []
    with session_scope() as db:
        repo = IncidentRepo(db)
        for ev in events:
            if repo.is_duplicate(device_id, ev.dedup_key, ev.legacy_window_s, ev.occurred_at):
                stats["duplicates"] += 1
                continue
            inc = repo.ensure(ev.correlation_key, device_id=device_id, account_id=account_id,
                              occurred_at=ev.occurred_at, session_id=ev.session_id, turn_id=ev.turn_id,
                              fw_version=fw, detected_by=ev.detected_by)
            c = classify_event(ev.kind, ev.reason, ev.detail, ev.detected_by)
            dedup = ev.dedup_key
            if dedup and ev.legacy_window_s:
                dedup = f"{dedup}:{int(ev.occurred_at.timestamp())}"
            row = DeviceIssue(device_id=device_id, account_id=account_id, kind=ev.kind, severity=c.severity,
                              reason=ev.reason, detail=ev.detail, fw_version=fw, incident_id=inc.id,
                              detected_by=ev.detected_by, category=c.category, confidence=c.confidence,
                              suspected_component=c.component, reason_code=c.reason_code, session_id=ev.session_id,
                              turn_id=ev.turn_id, occurred_at=ev.occurred_at, dedup_key=dedup)
            if not repo.add_event(row):  # the same report again (raced past is_duplicate)
                stats["duplicates"] += 1
                continue
            inc = repo.by_key(ev.correlation_key, lock=True) or inc
            _regroup(repo, inc)
            if inc.recovered_at is None and (ev.recovered or _reconnected_since(db, device_id, ev.session_id)):
                inc.recovered_at = utcnow()
            if fw and not inc.fw_version:
                inc.fw_version = fw
            db.add(inc)
            db.commit()
            stats["stored"] += 1
            if inc.id not in changed:
                changed.append(inc.id)  # type: ignore[arg-type]
    return changed


def _reconnected_since(db: Any, device_id: str, session_id: str | None) -> bool:
    """The watch has a newer session than the one this event is about (read fresh, right before the commit:
    a hello that commits in between marks this incident itself - see session_started)."""
    if not session_id:
        return False
    latest = db.exec(select(Device.last_session_id).where(Device.id == device_id)).first()
    return bool(latest) and latest != session_id


def _regroup(repo: IncidentRepo, inc: DiagIncident) -> None:
    """Recompute the incident from all its events (any arrival order gives the same result)."""
    evs = list(repo.events(inc.id))  # type: ignore[arg-type]
    c = classify_incident([{"kind": e.kind, "reason": e.reason, "detail": e.detail, "detected_by": e.detected_by,
                            "dedup_key": e.dedup_key} for e in evs])
    inc.category, inc.confidence, inc.reason_code = c.category, c.confidence, c.reason_code
    inc.suspected_component, inc.severity, inc.detected_by = c.component, c.severity, c.detected_by
    inc.primary_event_id = evs[c.primary_index].id if c.primary_index is not None and evs else None
    inc.event_count = len(evs)
    times = [_aware(e.occurred_at or e.created_at) for e in evs]
    inc.occurred_at = min(t for t in times if t is not None) if evs else inc.occurred_at
    if inc.turn_id is None:
        inc.turn_id = next((e.turn_id for e in evs if e.turn_id is not None), None)
    if inc.session_id is None:
        inc.session_id = next((e.session_id for e in evs if e.session_id), None)
    inc.updated_at = utcnow()


def session_started(device_id: str, session_id: str) -> int:
    """A new authenticated session: earlier connection, restart and turn incidents of this watch recovered."""
    with session_scope() as db:
        n = IncidentRepo(db).mark_recovered(device_id, utcnow(), exclude_session=session_id)
        db.commit()
        return n


def turn_succeeded(device_id: str) -> int:
    """A turn completed: failed-turn incidents of this watch recovered."""
    with session_scope() as db:
        n = IncidentRepo(db).mark_recovered(device_id, utcnow(), turns_only=True)
        db.commit()
        return n


_RETRYABLE = {"record": lambda *a: record(*a), "session_started": lambda *a: session_started(*a),
              "turn_succeeded": lambda *a: turn_succeeded(*a)}


def _enqueue(fn: str, *args: Any) -> None:
    with _retry_lock:
        if len(_retry) == _retry.maxlen:
            stats["dropped"] += 1
            log.warning("diagnostics: retry queue full, oldest pending write dropped")
        _retry.append((fn, args))
        stats["queued"] += 1


def flush_retry() -> list[int]:
    """Store what waits in the retry queue (oldest first); stops at the first failure."""
    changed: list[int] = []
    while True:
        with _retry_lock:
            if not _retry:
                return changed
            item = _retry.popleft()
        fn, args = item
        try:
            out = _RETRYABLE[fn](*args)
            if fn == "record":
                changed += out
        except Exception:  # noqa: BLE001
            with _retry_lock:
                _retry.appendleft(item)
            raise


def pending() -> int:
    return len(_retry)


# --- async entry points (gateway) ------------------------------------------------------------------------


async def record_now(hub: Any, device_id: str, account_id: int | None, fw: str, events: list[Event],
                     *, started_session: str | None = None, turn_ok: bool = False) -> None:
    """Store and publish; never raises. Events that cannot be stored now are queued for a retry."""
    if not events and not started_session and not turn_ok:
        return
    changed: list[int] = []
    steps: list[tuple[str, tuple]] = []
    if started_session:
        steps.append(("session_started", (device_id, started_session)))
    if turn_ok:
        steps.append(("turn_succeeded", (device_id,)))
    if events:
        steps.append(("record", (device_id, account_id, fw, events)))
    for fn, args in steps:
        try:
            out = await asyncio.to_thread(_RETRYABLE[fn], *args)
            if fn == "record":
                changed = out
        except Exception:  # noqa: BLE001 - diagnostics must never break the session
            stats["failed"] += 1
            log.warning("diagnostics: %s for %s failed; queued for retry", fn, device_id, exc_info=True)
            _enqueue(fn, *args)
    await publish(hub, changed)


def record_soon(hub: Any, device_id: str, account_id: int | None, fw: str, events: list[Event], **kw: Any) -> None:
    """Fire-and-forget record_now (the caller does not wait for the database)."""
    task = asyncio.create_task(record_now(hub, device_id, account_id, fw, events, **kw), name=f"diag-{device_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain() -> None:
    """Wait for the pending fire-and-forget writes (shutdown, tests)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def publish(hub: Any, incident_ids: list[int]) -> None:
    if not incident_ids or hub is None:
        return
    try:
        rows = await asyncio.to_thread(_load_out, incident_ids)
    except Exception:  # noqa: BLE001
        log.warning("diagnostics: live update skipped", exc_info=True)
        return
    for out in rows:
        hub.publish({"type": "incident", "device_id": out["device_id"], "incident": out}, operator_only=True)


def _load_out(ids: list[int]) -> list[dict[str, Any]]:
    with session_scope() as db:
        out = []
        for i in ids:
            inc = db.get(DiagIncident, i)
            if inc is not None:
                dev = db.get(Device, inc.device_id)
                out.append(incident_out(inc, dev.name if dev else None))
        return out


async def maintenance_loop(hub: Any) -> None:
    """Retry queued events; apply the retention period (hourly)."""
    last_prune = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            if _retry:
                changed = await asyncio.to_thread(flush_retry)
                await publish(hub, changed)
        except Exception:  # noqa: BLE001
            log.warning("diagnostics: retry failed (%d waiting)", len(_retry), exc_info=True)
        if loop.time() - last_prune >= PRUNE_INTERVAL_S:
            try:
                n = await asyncio.to_thread(prune)
                last_prune = loop.time()
                if n:
                    log.info("diagnostics: %d incident(s) past retention removed", n)
            except Exception:  # noqa: BLE001
                log.warning("diagnostics: retention cleanup failed", exc_info=True)
        await asyncio.sleep(RETRY_INTERVAL_S)


def prune() -> int:
    days = get_settings().incident_retention_days
    if days <= 0:
        return 0
    with session_scope() as db:
        return IncidentRepo(db).prune(utcnow() - timedelta(days=days))


# --- API views -------------------------------------------------------------------------------------------


def incident_out(inc: DiagIncident, device_name: str | None = None) -> dict[str, Any]:
    _, cause, _ = texts.reason(inc.reason_code)
    return {
        "id": inc.id,
        "device_id": inc.device_id,
        "device_name": device_name,
        "category": inc.category,
        "severity": inc.severity,
        "confidence": inc.confidence,
        "reason_code": inc.reason_code,
        "title": texts.title(inc.reason_code),
        "cause": cause,
        "suspected_component": inc.suspected_component,
        "detected_by": inc.detected_by,
        "session_id": inc.session_id,
        "turn_id": inc.turn_id,
        "fw_version": inc.fw_version,
        "event_count": inc.event_count,
        "related_count": max(0, inc.event_count - 1),
        "legacy": inc.legacy,
        "occurred_at": _aware(inc.occurred_at),
        "recovered_at": _aware(inc.recovered_at),
        "updated_at": _aware(inc.updated_at),
    }


def event_out(e: DeviceIssue, primary_id: int | None) -> dict[str, Any]:
    return {
        "id": e.id,
        "kind": e.kind,
        "reason": e.reason,
        "detected_by": e.detected_by,
        "category": e.category,
        "confidence": e.confidence,
        "reason_code": e.reason_code,
        "title": texts.title(e.reason_code) if e.reason_code else e.kind,
        "suspected_component": e.suspected_component,
        "severity": e.severity,
        "session_id": e.session_id,
        "turn_id": e.turn_id,
        "fw_version": e.fw_version,
        "detail": e.detail or {},
        "occurred_at": _aware(e.occurred_at or e.created_at),
        "received_at": _aware(e.created_at),
        "primary": e.id == primary_id,
    }


def detail_out(inc: DiagIncident, events: list[DeviceIssue], device_name: str | None) -> dict[str, Any]:
    out = incident_out(inc, device_name)
    out["explanation"] = texts.explain(inc, events)
    out["events"] = [event_out(e, inc.primary_event_id) for e in events]
    return out
