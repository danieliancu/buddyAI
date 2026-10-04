"""mDNS follows the PC's LAN address (a phone hotspot hands out a new one on reconnect)."""

import asyncio

import app.discovery as discovery
from app.discovery import MdnsAdvertiser


def test_new_lan_address_is_advertised_again(monkeypatch):
    monkeypatch.setattr(discovery, "RECHECK_S", 0)
    adv = MdnsAdvertiser("10.44.49.61", 8765)
    calls: list[str] = []

    async def start():
        calls.append(f"start {adv.ip}")

    async def stop():
        calls.append("stop")

    adv.start, adv.stop = start, stop
    ips = iter(["10.44.49.61", "10.135.162.61", "10.135.162.61"])

    async def run():
        task = asyncio.create_task(adv.follow(lambda: next(ips, "10.135.162.61")))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(run())
    assert calls == ["stop", "start 10.135.162.61"]
