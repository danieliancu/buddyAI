"""Calling the item tools the way a voice turn does (authenticated call context)."""

from __future__ import annotations

import json
from typing import Any

from app.items import AssistantTools
from app.voice_tools import ToolCallCtx


class Voice:
    """One watch session of one account: run(tool, args) like the model would, turn by turn."""

    def __init__(self, account_id: int, tz: str = "Europe/London", device: str = "dev-t", session: str = "sess-t",
                 mode: str = "chat") -> None:
        self.account_id, self.tz, self.device, self.session, self.mode = account_id, tz, device, session, mode
        self.turn = 1
        self.tools = AssistantTools()

    def call(self, turn: int | None = None) -> ToolCallCtx:
        return ToolCallCtx(self.account_id, self.device, self.session, turn or self.turn, self.tz, self.mode, "ro")

    def run(self, name: str, args: dict[str, Any] | str, turn: int | None = None):
        raw = args if isinstance(args, str) else json.dumps(args)
        return self.tools.execute(self.account_id, self.tz, name, raw, self.device, self.call(turn))

    def body(self, name: str, args: dict[str, Any] | str, turn: int | None = None) -> dict[str, Any]:
        return json.loads(self.run(name, args, turn).result)

    def next_turn(self) -> int:
        self.turn += 1
        return self.turn


def by_number(kind: str, number: int) -> dict[str, Any]:
    """target for "the item shown as #n on the watch" (the user said its number)."""
    return {"query": {"kind": kind, "number": number}}
