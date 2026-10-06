"""ola server entry point: `python -m app.main` (or `uvicorn app.main:app`)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import languages, usage_ops
from app.api import accounts_admin, auth, devices, finance, firmware, issues, live, me, shop, system, usage
from app.api import memory as memory_api
from app.config import get_settings, load_providers_config
from app.db.repositories import PersonaRepo, PricingRepo
from app.db.session import run_migrations, session_scope
from app.gateway import device_ws
from app.gateway.hub import DeviceHub
from app.items import AssistantTools
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.providers.router import ProviderRouter
from app.reminders import reminder_loop

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
        tools=AssistantTools(),
    )

    reminders = asyncio.create_task(reminder_loop(app.state.hub), name="reminders")
    # Usage operations: queued settlements and expired leases (each process runs it; see app/usage_ops.py).
    usage_maint = asyncio.create_task(usage_ops.maintenance_loop(), name="usage-maintenance")
    # Long-term memory: vectors and opt-in learning (durable jobs; idle unless BUDDYAI_MEMORY_ENABLED).
    from app.memory.jobs import job_loop

    memory_jobs = asyncio.create_task(job_loop(app.state.router), name="memory-jobs")

    # Build the language detector in the background so the first "auto" turn doesn't wait for it.
    warmup = asyncio.create_task(asyncio.to_thread(languages.warm_up))

    mdns = None
    if settings.mdns_enabled:
        from app.api.system import lan_ip
        from app.discovery import MdnsAdvertiser

        mdns = MdnsAdvertiser(lan_ip(), settings.port)
        await mdns.start()
        mdns_follow = asyncio.create_task(mdns.follow(lan_ip))  # new LAN address -> advertised again
    log.info("ola server ready. Device endpoint: ws://<this-host>:%s/ws/device", settings.port)
    if settings.mock_providers:
        log.warning("MOCK providers enabled: no real STT/LLM/TTS calls")
    yield
    warmup.cancel()
    reminders.cancel()
    usage_maint.cancel()
    memory_jobs.cancel()
    # Graceful shutdown: running turns stop now (not charged, reason "shutdown") and are settled here;
    # what cannot be settled is expired by another process (or this one after a restart) when its lease ends.
    for conn in list(app.state.hub.connections.values()):
        if conn.active is not None:
            conn.active.abort_reason = "shutdown"
            with contextlib.suppress(Exception):
                await conn._cancel_active()
    with contextlib.suppress(Exception):
        await asyncio.to_thread(usage_ops.flush_pending)
    if mdns:
        mdns_follow.cancel()
        await mdns.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="ola Server", version="0.1.0", lifespan=lifespan)
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
        memory_api.router,
        me.router,
        shop.router,
        accounts_admin.router,
        devices.router,
        usage.router,
        finance.router,
        system.router,
        firmware.router,
        issues.router,
        live.router,
        device_ws.router,
    ):
        app.include_router(r)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        """CSRF defence in depth (cookies are already SameSite=Strict): browser writes to /api must
        come from our own origin. Requests without an Origin header (Stripe webhook, curl, the
        watch) are unaffected."""
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        """Load-balancer / uptime check: also verifies the database answers."""
        try:
            with session_scope() as db:
                db.exec(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "db": False}, status_code=503)
        if not usage_ops.database_ready():  # AI admissions need the billing database
            return JSONResponse({"ok": False, "db": True, "billing_db": False}, status_code=503)
        return JSONResponse({"ok": True, "db": True})

    dist = settings.web_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith(("api/", "ws/", "fw/")):
                # Unknown API route: a JSON 404, never the web page (it would break the app silently).
                raise HTTPException(404, "not found")
            file = dist / path
            if path and file.is_file() and dist in file.resolve().parents:
                return FileResponse(file)
            return FileResponse(dist / "index.html")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    s = get_settings()
    if s.log_json:
        from app.logging_json import configure_json_logging

        configure_json_logging()
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        "app.main:app",
        host=s.host,
        port=s.port,
        ws_max_size=1 << 20,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips=s.forwarded_allow_ips,
        log_config=None if s.log_json else uvicorn.config.LOGGING_CONFIG,
    )


if __name__ == "__main__":
    main()
