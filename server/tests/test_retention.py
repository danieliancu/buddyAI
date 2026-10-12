"""Retention policy (app/retention.py): defaults equal the behaviour before it existed; each step deletes only
what is past its period; 0 means keep; runs are idempotent and one failing step doesn't stop the others."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app import retention
from app.config import Settings, get_settings
from app.db.models import AuditLog, Conversation, Memory, SearchCache, Turn, UsageRecord, VoiceOperation
from app.db.repositories import DeviceRepo, VoiceOpRepo
from app.db.session import session_scope
from app.retention import RetentionPolicy, run_retention_once
from app.security import hash_device_token
from tests.test_items import _account

NOW = datetime.now(timezone.utc)


def test_defaults_equal_the_previous_behaviour() -> None:
    p = RetentionPolicy.from_settings(Settings())
    assert p == RetentionPolicy(conversation_days=0, incident_days=90, voice_ops_days=7, memory_superseded_days=90,
                                memory_jobs_days=7, audit_days=0, search_cache_grace_days=1)
    assert VoiceOpRepo.KEEP_DAYS == p.voice_ops_days
    assert Settings().retention_interval_s == 3600


def _voice_op(acc: int, age_days: float) -> str:
    key = secrets.token_hex(16)
    with session_scope() as db:
        db.add(VoiceOperation(op_key=key, account_id=acc, device_id="d", session_id="s", turn_id=1, tool="t",
                              created_at=NOW - timedelta(days=age_days)))
        db.commit()
    return key


def _exists(model, **where) -> bool:
    with session_scope() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.exec(q).first() is not None


def test_voice_operations_older_than_the_period_are_deleted() -> None:
    acc = _account()
    old, new = _voice_op(acc, 8), _voice_op(acc, 6)
    run_retention_once(RetentionPolicy())
    assert not _exists(VoiceOperation, op_key=old)
    assert _exists(VoiceOperation, op_key=new)


def _cache_row(expired_days_ago: float) -> str:
    key = secrets.token_hex(16)
    with session_scope() as db:
        db.add(SearchCache(key=key, scope="shared", category="weather", answer="a",
                           expires_at=NOW - timedelta(days=expired_days_ago)))
        db.commit()
    return key


def test_expired_search_cache_rows_are_purged_after_the_grace_period() -> None:
    long_gone, just_expired, fresh = _cache_row(2), _cache_row(0.5), _cache_row(-1)
    run_retention_once(RetentionPolicy())
    assert not _exists(SearchCache, key=long_gone)
    assert _exists(SearchCache, key=just_expired) and _exists(SearchCache, key=fresh)


def _history(acc: int, age_days: float) -> tuple[str, int, int, int]:
    """A watch with one conversation and one turn of this age, a usage record and a memory linked to it."""
    dev = f"dev-ret-{secrets.token_hex(4)}"
    at = NOW - timedelta(days=age_days)
    with session_scope() as db:
        DeviceRepo(db).pair(dev, hash_device_token(secrets.token_hex(8)), "Watch", "t", "t", acc)
        conv = Conversation(device_id=dev, started_at=at, last_activity_at=at)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        turn = Turn(device_id=dev, account_id=acc, conversation_id=conv.id, session_id="s", turn_no=1, language="en",
                    user_text="old secret", assistant_text="old answer", created_at=at)
        db.add(turn)
        db.commit()
        db.refresh(turn)
        usage = UsageRecord(turn_id=turn.id, device_id=dev, account_id=acc, kind="llm", provider="p", model="m",
                            unit="u", quantity=1, created_at=at)
        mem = Memory(account_id=acc, kind="other", content=f"fact {secrets.token_hex(3)}",
                     content_hash=secrets.token_hex(32), origin="explicit", source_turn_id=turn.id)
        db.add(usage)
        db.add(mem)
        db.commit()
        return dev, turn.id, usage.id, mem.id


def test_conversations_are_kept_when_the_period_is_zero() -> None:
    acc = _account()
    _dev, turn_id, _u, _m = _history(acc, 4000)
    run_retention_once(RetentionPolicy(conversation_days=0))
    assert _exists(Turn, id=turn_id)


def test_conversations_older_than_the_period_are_deleted_usage_and_memories_kept() -> None:
    acc = _account()
    dev_old, old_turn, usage_id, mem_id = _history(acc, 40)
    _dev_new, new_turn, _u, _m = _history(acc, 5)
    out = run_retention_once(RetentionPolicy(conversation_days=30))
    assert out["conversations"] >= 1
    assert not _exists(Turn, id=old_turn) and _exists(Turn, id=new_turn)
    assert not _exists(Conversation, device_id=dev_old)
    with session_scope() as db:
        assert db.get(UsageRecord, usage_id).turn_id is None  # accounting kept, link removed
        assert db.get(Memory, mem_id).source_turn_id is None


def _audit(age_days: float) -> int:
    with session_scope() as db:
        row = AuditLog(actor="system", action="test.retention", created_at=NOW - timedelta(days=age_days))
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def test_audit_log_kept_when_zero_and_pruned_when_set() -> None:
    old = _audit(5000)
    run_retention_once(RetentionPolicy(audit_days=0))
    assert _exists(AuditLog, id=old)
    recent = _audit(1)
    run_retention_once(RetentionPolicy(audit_days=365))
    assert not _exists(AuditLog, id=old) and _exists(AuditLog, id=recent)


def test_running_twice_is_idempotent() -> None:
    acc = _account()
    _voice_op(acc, 30)
    _cache_row(10)
    policy = RetentionPolicy(conversation_days=30)
    _history(acc, 60)
    first = run_retention_once(policy)
    assert first["voice_operations"] >= 1 and first["search_cache"] >= 1 and first["conversations"] >= 1
    second = run_retention_once(policy)
    assert second == {"voice_operations": 0, "search_cache": 0, "conversations": 0, "audit_log": 0}


def test_a_failing_step_does_not_stop_the_others(monkeypatch) -> None:
    acc = _account()
    old = _voice_op(acc, 30)
    gone = _cache_row(10)

    def boom(policy, now):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(retention, "prune_voice_ops", boom)
    out = run_retention_once(RetentionPolicy())
    assert out["voice_operations"] is None
    assert out["search_cache"] >= 1 and not _exists(SearchCache, key=gone)
    assert _exists(VoiceOperation, op_key=old)  # the failed step changed nothing


def test_settings_feed_the_policy(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "voice_ops_retention_days", 3)
    monkeypatch.setattr(s, "conversation_retention_days", 365)
    p = RetentionPolicy.from_settings()
    assert p.voice_ops_days == 3 and p.conversation_days == 365
