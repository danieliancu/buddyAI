"""DeviceHub: connected devices, pending pairings and live events for the web UI."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.db.models import Item
from app.db.repositories import DeviceRepo, ItemRepo, SettingsRepo
from app.db.session import session_scope
from app.device_settings import device_view
from app.items import device_full, device_snapshot
from app.security import hash_device_token, new_device_token

if TYPE_CHECKING:
    from app.gateway.device_ws import DeviceConnection

log = logging.getLogger(__name__)


@dataclass
class PendingPairing:
    device_id: str
    hw_model: str
    fw_version: str
    conn: "DeviceConnection"
    expires_at: float


class PairingError(Exception):
    pass


class DeviceHub:
    def __init__(self, pairing_ttl_s: int = 300) -> None:
        self.pairing_ttl_s = pairing_ttl_s
        self.connections: dict[str, "DeviceConnection"] = {}
        self.pending: dict[str, PendingPairing] = {}
        # Live-event subscribers: queue -> account filter (None = operator, sees everything)
        self._listeners: dict[asyncio.Queue, int | None] = {}
        self.owners: dict[str, int | None] = {}  # device_id -> account_id (for event filtering)

    # --- live events (web UI) --------------------------------------------------------

    def subscribe(self, account_id: int | None = None) -> asyncio.Queue:
        """account_id=None: operator (all events); otherwise only that account's watches."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._listeners[q] = account_id
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.pop(q, None)

    def publish(self, event: dict[str, Any], operator_only: bool = False) -> None:
        event.setdefault("at", int(time.time() * 1000))
        owner = event["account_id"] if "account_id" in event else self._owner_of(event.get("device_id"))
        for q, account_id in list(self._listeners.items()):
            if account_id is not None and (operator_only or account_id != owner):
                continue
            if q.full():
                continue  # slow browser tab: drop instead of blocking the device path
            q.put_nowait(event)

    def _owner_of(self, device_id: str | None) -> int | None:
        if not device_id:
            return None
        if device_id not in self.owners:
            with session_scope() as db:
                dev = DeviceRepo(db).get(device_id)
                self.owners[device_id] = dev.account_id if dev else None
        return self.owners[device_id]

    def forget_owner(self, device_id: str) -> None:
        self.owners.pop(device_id, None)

    # --- connections ------------------------------------------------------------------

    async def register(self, conn: "DeviceConnection") -> None:
        old = self.connections.get(conn.device_id)
        self.connections[conn.device_id] = conn
        self.owners[conn.device_id] = conn.account_id
        if old and old is not conn:
            await old.close(code=4000, reason="replaced by a newer connection")
        self.publish({"type": "device_online", "device_id": conn.device_id})

    def unregister(self, conn: "DeviceConnection") -> None:
        if self.connections.get(conn.device_id) is conn:
            del self.connections[conn.device_id]
            self.publish({"type": "device_offline", "device_id": conn.device_id})
        for code, p in list(self.pending.items()):
            if p.conn is conn:
                del self.pending[code]

    def is_online(self, device_id: str) -> bool:
        return device_id in self.connections

    def state_of(self, device_id: str) -> str | None:
        conn = self.connections.get(device_id)
        return conn.ui_state if conn else None

    # --- pairing ----------------------------------------------------------------------

    def add_pending(self, code: str, conn: "DeviceConnection", hw_model: str, fw: str) -> None:
        self._expire()
        existing = self.pending.get(code)
        if existing and existing.conn is not conn:
            raise PairingError("code already in use")
        self.pending[code] = PendingPairing(conn.device_id, hw_model, fw, conn, time.monotonic() + self.pairing_ttl_s)
        self.publish({"type": "pairing_pending", "device_id": conn.device_id})

    def list_pending(self) -> list[dict[str, Any]]:
        self._expire()
        return [
            {"device_id": p.device_id, "hw_model": p.hw_model, "expires_in_s": int(p.expires_at - time.monotonic())}
            for p in self.pending.values()
        ]

    def _expire(self) -> None:
        now = time.monotonic()
        for code, p in list(self.pending.items()):
            if p.expires_at < now:
                del self.pending[code]

    async def pair(self, code: str, name: str, account_id: int | None = None) -> str:
        """Accept a pairing code (entered by the owner or the operator). Returns the device_id."""
        self._expire()
        p = self.pending.pop(code, None)
        if p is None:
            raise PairingError("invalid or expired code")
        token = new_device_token()
        with session_scope() as db:
            DeviceRepo(db).pair(p.device_id, hash_device_token(token), name, p.hw_model, p.fw_version, account_id)
            SettingsRepo(db).ensure(p.device_id)
        await p.conn.send_json("paired", device_token=token)
        self.owners[p.device_id] = account_id
        self.publish({"type": "device_paired", "device_id": p.device_id})
        return p.device_id

    # --- pushes -----------------------------------------------------------------------

    async def push_settings(self, device_id: str) -> None:
        conn = self.connections.get(device_id)
        if not conn:
            return
        with session_scope() as db:
            settings, version = SettingsRepo(db).get(device_id)
        conn.settings = settings
        await conn.send_json("settings_update", settings=device_view(settings), settings_version=version)

    def account_connections(self, account_id: int) -> list["DeviceConnection"]:
        return [c for c in self.connections.values() if c.authenticated and c.account_id == account_id]

    async def push_items(self, account_id: int, only: "DeviceConnection | None" = None) -> None:
        """Send the notes/reminders snapshot to the account's watches (or just `only`)."""
        conns = [only] if only else self.account_connections(account_id)
        if not conns:
            return
        with session_scope() as db:
            items = ItemRepo(db).list(account_id)
        for c in conns:
            await c.send_json("items", **device_snapshot(items, c.settings.timezone))

    async def push_usage_notice(self, account_id: int) -> bool:
        """A usage threshold not yet shown on a watch: a short `notice` to the account's watches.

        The watch keeps it until no conversation is running, so it never interrupts one. Old firmware
        ignores the message type. Shown once per threshold and period (usage_notices)."""
        conns = self.account_connections(account_id)
        if not conns:
            return False
        from app import usage_notices  # avoid an import cycle

        notice = usage_notices.take_watch_notice(account_id)
        if notice is None:
            return False
        for c in conns:
            await c.send_json("notice", level=notice["level"], text=notice["text"])
        return True

    def items_changed(self, account_id: int) -> None:
        """Tell the account's open web pages to reload notes/reminders."""
        self.publish({"type": "items_changed", "account_id": account_id})

    async def fire_reminder(self, item: Item, early: bool = False) -> bool:
        """`early`: the advance notice (notify_before_min before the start), not the alert at the start."""
        delivered = False
        for c in self.account_connections(item.account_id):
            if await c.send_json("reminder_fire", item=device_full(item, c.settings.timezone), early=early):
                delivered = True
        return delivered

    async def revoke(self, device_id: str) -> None:
        conn = self.connections.get(device_id)
        if conn:
            await conn.send_json("error", code="unauthorized", message="device revoked")
            await conn.close(code=4001, reason="revoked")

    async def disconnect(self, device_id: str, reason: str) -> None:
        """Close the session without invalidating the token (the watch reconnects)."""
        conn = self.connections.get(device_id)
        if conn:
            await conn.close(code=4002, reason=reason)

    async def offer_ota(self, device_id: str, offer: dict[str, Any]) -> bool:
        conn = self.connections.get(device_id)
        if not conn:
            return False
        await conn.send_json("ota_available", **offer)
        return True
