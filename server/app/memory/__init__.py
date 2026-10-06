"""Long-term memory: facts the user asked the assistant to keep (and, opt-in, facts learned from
conversations), recalled in later conversations. See the plan in docs/MEMORY.md."""

from __future__ import annotations

from app.config import get_settings


def prefs(account_id: int | None) -> tuple[bool, bool]:
    """(explicit memory on, learning on) for this account: the server switches and the account's choice."""
    s = get_settings()
    if not s.memory_on_for(account_id):
        return False, False
    from app.db.models import Account
    from app.db.session import session_scope

    try:
        with session_scope() as db:
            acc = db.get(Account, account_id)
            if acc is None or acc.status != "active":
                return False, False
            return bool(acc.memory_explicit), bool(acc.memory_learn and s.memory_inference_enabled)
    except Exception:  # noqa: BLE001 - memory never blocks a turn
        return False, False
