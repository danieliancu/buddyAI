"""GET /api/me/badge: the marketing site's header shows the signed-in customer's first name. Only our own
site origin may read it (CORS); it never reveals anything but the first name."""

import secrets

from fastapi.testclient import TestClient

from app.config import get_settings
from app.email import ConsoleEmailSender
from app.main import app
from app.ratelimit import SIGNUP_PER_IP

SITE = "https://www.example.com"


def test_badge_signed_out_and_in(monkeypatch):
    monkeypatch.setattr(get_settings(), "site_url", SITE)
    SIGNUP_PER_IP._hits.clear()
    with TestClient(app) as c:
        r = c.get("/api/me/badge", headers={"Origin": SITE})
        assert r.status_code == 200 and r.json() == {"signed_in": False}
        assert r.headers["access-control-allow-origin"] == SITE
        assert r.headers["access-control-allow-credentials"] == "true"
        assert r.headers["cache-control"] == "no-store"

        addr = f"badge-{secrets.token_hex(3)}@example.com"
        assert c.post("/api/me/signup", json={"email": addr, "password": "correct-horse-1", "name": "Jane Buyer"}).status_code == 200
        r = c.get("/api/me/badge", headers={"Origin": SITE})
        assert r.json() == {"signed_in": True, "name": "Jane", "last_name": "Buyer"}

        evil = c.get("/api/me/badge", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in evil.headers  # the browser hides the answer from other sites
        assert "Origin" in evil.headers["vary"]
    assert any(m.to == addr for m in ConsoleEmailSender.sent)


def test_remember_me_off_gives_a_browser_session_cookie(monkeypatch):
    SIGNUP_PER_IP._hits.clear()
    from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP

    LOGIN_PER_ACCOUNT._hits.clear()
    LOGIN_PER_IP._hits.clear()
    with TestClient(app) as c:
        addr = f"remember-{secrets.token_hex(3)}@example.com"
        assert c.post("/api/me/signup", json={"email": addr, "password": "correct-horse-1"}).status_code == 200
        c.post("/api/me/logout")
        kept = c.post("/api/me/login", json={"email": addr, "password": "correct-horse-1"})
        assert "max-age=" in kept.headers["set-cookie"].lower()  # remember me (default): 14 days
        c.post("/api/me/logout")
        short = c.post("/api/me/login", json={"email": addr, "password": "correct-horse-1", "remember": False})
        cookie = short.headers["set-cookie"].lower()
        assert cookie.startswith("buddyai_session=") and "max-age=" not in cookie and "expires=" not in cookie
        assert c.get("/api/me").status_code == 200  # still signed in until the browser closes
