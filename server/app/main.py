"""BuddyAI server entry point: `python -m app.main` (or `uvicorn app.main:app`)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import languages
from app.api import accounts_admin, auth, devices, firmware, live, me, system, usage
from app.config import get_settings, load_providers_config
from app.db.repositories import PersonaRepo, PricingRepo
from app.db.session import run_migrations, session_scope
from app.gateway import device_ws
from app.gateway.hub import DeviceHub
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.providers.router import ProviderRouter

log = logging.getLogger("buddyai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    config = load_providers_config()
    run_migrations()
    with session_scope() as db:
        PersonaRepo(db).seed()
        PricingRepo(db).seed(config.get("pricing_defaults", []))

    app.state.hub = DeviceHub(settings.pairing_code_ttl_s)
    app.state.router = ProviderRouter(settings, config)
    chunker_config = ChunkerConfig.from_dict(config.get("chunker", {}))
    chunker_config.abbreviations = languages.abbreviations()
    app.state.pipeline = ConversationPipeline(
        app.state.router,
        chunker_config,
        downlink_bitrate=config["audio"]["opus_downlink_bitrate"],
    )

    # Build the language detector in the background so the first "auto" turn doesn't wait for it.
    warmup = asyncio.create_task(asyncio.to_thread(languages.warm_up))

    mdns = None
    if settings.mdns_enabled:
        from app.api.system import lan_ip
        from app.discovery import MdnsAdvertiser

        mdns = MdnsAdvertiser(lan_ip(), settings.port)
        await mdns.start()
    log.info("BuddyAI server ready. Device endpoint: ws://<this-host>:%s/ws/device", settings.port)
    if settings.mock_providers:
        log.warning("MOCK providers enabled: no real STT/LLM/TTS calls")
    yield
    warmup.cancel()
    if mdns:
        await mdns.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="BuddyAI Server", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="buddyai_session",
        same_site="strict",
        https_only=settings.public_url.startswith("https"),
        max_age=14 * 24 * 3600,
    )
    for r in (
        auth.router,
        me.router,
        accounts_admin.router,
        devices.router,
        usage.router,
        system.router,
        firmware.router,
        live.router,
        device_ws.router,
    ):
        app.include_router(r)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    dist = settings.web_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            file = dist / path
            if path and file.is_file() and dist in file.resolve().parents:
                return FileResponse(file)
            return FileResponse(dist / "index.html")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, ws_max_size=1 << 20, log_level="info")


if __name__ == "__main__":
    main()
