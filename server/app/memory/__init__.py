"""Long-term memory: facts the user asked the assistant to keep (and, opt-in, facts learned from
conversations), recalled in later conversations. See docs/MEMORY.md."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class Prefs:
    """What memory may do for an account: the server switches combined with the account's own choices.

    available: memory exists for this account (the server switches); listing, correcting and forgetting work.
    save: "remember that..." is stored (account switch, on by default).
    use: memories are recalled in conversations (account switch, on by default, separate from saving).
    learn: facts are learned from conversations (opt-in: off until the account switches it on).
    """

    available: bool = False
    save: bool = False
    use: bool = False
    learn: bool = False


OFF = Prefs()


def prefs(account_id: int | None) -> Prefs:
    s = get_settings()
    if not s.memory_on_for(account_id):
        return OFF
    from app.db.models import Account
    from app.db.session import session_scope

    try:
        with session_scope() as db:
            acc = db.get(Account, account_id)
            if acc is None or acc.status != "active":
                return OFF
            return Prefs(True, bool(acc.memory_explicit), bool(acc.memory_use),
                         bool(acc.memory_learn and s.memory_inference_enabled))
    except Exception:  # noqa: BLE001 - memory never blocks a turn
        return OFF
