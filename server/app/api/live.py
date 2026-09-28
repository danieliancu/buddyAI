"""/api/live: WebSocket with live device events for the web UI (admin session required)."""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/api/live")
async def live(ws: WebSocket) -> None:
    if not ws.session.get("admin"):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub = ws.app.state.hub
    queue = hub.subscribe()

    async def drain_client() -> None:
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                await ws.receive_text()

    reader = asyncio.create_task(drain_client())
    try:
        while not reader.done():
            try:
                event = await asyncio.wait_for(queue.get(), 20)
            except asyncio.TimeoutError:
                event = {"type": "keepalive"}
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(queue)
        reader.cancel()
