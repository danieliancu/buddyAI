"""Optional mDNS advertisement (_buddyai._tcp) for local development. Never required by devices."""

from __future__ import annotations

import logging
import socket

from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from app.gateway.protocol import PROTOCOL_VERSION

log = logging.getLogger(__name__)
SERVICE_TYPE = "_buddyai._tcp.local."


class MdnsAdvertiser:
    def __init__(self, ip: str, port: int) -> None:
        self.ip, self.port = ip, port
        self._zc: AsyncZeroconf | None = None
        self._info: AsyncServiceInfo | None = None

    async def start(self) -> None:
        host = socket.gethostname().split(".")[0]
        self._info = AsyncServiceInfo(
            SERVICE_TYPE,
            f"BuddyAI {host}.{SERVICE_TYPE}",
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
