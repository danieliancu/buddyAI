"""DeviceHub: connected devices, pending pairings and live events for the web UI."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.db.repositories import DeviceRepo, SettingsRepo
from app.db.session import session_scope
from app.device_settings import device_view
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
        self._listeners: set[asyncio.Queue] = set()

    # --- live events (web UI) --------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        event.setdefault("at", int(time.time() * 1000))
        for q in list(self._listeners):
            if q.full():
                continue  # slow browser tab: drop instead of blocking the device path
            q.put_nowait(event)

    # --- connections ------------------------------------------------------------------

    async def register(self, conn: "DeviceConnection") -> None:
        old = self.connections.get(conn.device_id)
        self.connections[conn.device_id] = conn
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

    async def pair(self, code: str, name: str) -> str:
        """Accept a pairing code entered by the admin. Returns the device_id."""
        self._expire()
        p = self.pending.pop(code, None)
        if p is None:
            raise PairingError("invalid or expired code")
        token = new_device_token()
        with session_scope() as db:
            DeviceRepo(db).pair(p.device_id, hash_device_token(token), name, p.hw_model, p.fw_version)
            SettingsRepo(db).ensure(p.device_id)
        await p.conn.send_json("paired", device_token=token)
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

    async def revoke(self, device_id: str) -> None:
        conn = self.connections.get(device_id)
        if conn:
            await conn.send_json("error", code="unauthorized", message="device revoked")
            await conn.close(code=4001, reason="revoked")

    async def offer_ota(self, device_id: str, offer: dict[str, Any]) -> bool:
        conn = self.connections.get(device_id)
        if not conn:
            return False
        await conn.send_json("ota_available", **offer)
        return True
