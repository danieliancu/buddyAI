"""Optional mDNS advertisement (_buddyai._tcp) for local development. Never required by devices."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable

from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from app.gateway.protocol import PROTOCOL_VERSION

log = logging.getLogger(__name__)
SERVICE_TYPE = "_buddyai._tcp.local."
RECHECK_S = 15  # how often the LAN address is checked (a phone hotspot hands out a new one on reconnect)


class MdnsAdvertiser:
    def __init__(self, ip: str, port: int) -> None:
        self.ip, self.port = ip, port
        self._zc: AsyncZeroconf | None = None
        self._info: AsyncServiceInfo | None = None

    async def start(self) -> None:
        host = socket.gethostname().split(".")[0]
        self._info = AsyncServiceInfo(
            SERVICE_TYPE,
            f"ola {host}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(self.ip)],
            port=self.port,
            properties={"path": "/ws/device", "scheme": "ws", "proto": str(PROTOCOL_VERSION)},
            server=f"{host}.local.",
        )
        try:
            self._zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
            await self._zc.async_register_service(self._info)
            log.info("mDNS: advertising %s on %s:%s", SERVICE_TYPE, self.ip, self.port)
        except Exception as exc:  # pragma: no cover - network dependent
            log.warning("mDNS advertisement failed (%s); devices must use server_url", exc)
            self._zc = None

    async def stop(self) -> None:
        if self._zc and self._info:
            await self._zc.async_unregister_service(self._info)
            await self._zc.async_close()
        self._zc = None

    async def follow(self, current_ip: Callable[[], str]) -> None:
        """Re-advertise whenever the LAN address changes: otherwise watches discover the old address and
        stay on "Connecting" after the PC rejoins a hotspot. The sockets are rebuilt too (new interface)."""
        while True:
            await asyncio.sleep(RECHECK_S)
            ip = current_ip()
            if ip == self.ip or ip.startswith("127."):
                continue
            log.info("mDNS: LAN address changed %s -> %s, advertising again", self.ip, ip)
            try:
                await self.stop()
            except Exception as exc:  # pragma: no cover - network dependent
                log.warning("mDNS: could not withdraw the old address (%s)", exc)
            self.ip = ip
            await self.start()
