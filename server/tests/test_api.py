from fastapi.testclient import TestClient

from app.db.repositories import DeviceRepo, SettingsRepo
from app.db.session import session_scope
from app.main import app


def _client() -> TestClient:
    c = TestClient(app)
    c.__enter__()
    r = c.get("/api/auth/status").json()
    if r["needs_setup"]:
        c.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
    else:
        c.post("/api/auth/login", json={"username": "admin", "password": "password123"})
    return c


def test_settings_validation_returns_422_not_500():
    c = _client()
    with session_scope() as db:
        DeviceRepo(db).pair("api-dev-1", "hash-x", "t", "t", "t")
        SettingsRepo(db).ensure("api-dev-1")
    assert c.patch("/api/devices/api-dev-1/settings", json={"theme": {"accent": "red"}}).status_code == 422
    assert c.patch("/api/devices/api-dev-1/settings", json={"timezone": "Mars/Olympus"}).status_code == 422
    assert c.patch("/api/devices/api-dev-1/settings", json={"bogus": 1}).status_code == 422
    ok = c.patch("/api/devices/api-dev-1/settings", json={"volume": 40})
    assert ok.status_code == 200 and ok.json()["settings"]["volume"] == 40


def test_update_missing_persona_is_404():
    c = _client()
    r = c.put("/api/personas/9999", json={"name": "x", "system_prompt": "y", "is_default": False})
    assert r.status_code == 404


def test_cross_origin_writes_are_refused_and_headers_set():
    with TestClient(app) as c:
        evil = c.post("/api/me/login", json={"email": "a@b.co", "password": "x" * 8}, headers={"origin": "https://evil.example"})
        assert evil.status_code == 403
        same = c.post("/api/me/login", json={"email": "a@b.co", "password": "x" * 8}, headers={"origin": "http://testserver"})
        assert same.status_code == 401  # reached the handler
        r = c.get("/healthz")
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
        assert c.get("/readyz").json() == {"ok": True, "db": True}


def test_web_setup_can_be_disabled(monkeypatch):
    from app.config import get_settings
    from app.db.models import AdminUser
    from sqlmodel import delete

    monkeypatch.setattr(get_settings(), "allow_web_setup", False)
    with session_scope() as db:
        admins = db.exec(select_admins()).all()
        db.exec(delete(AdminUser))
        db.commit()
    try:
        with TestClient(app) as c:
            status = c.get("/api/auth/status").json()
            assert status["needs_setup"] is True and status["web_setup_allowed"] is False
            assert c.post("/api/auth/setup", json={"username": "intruder", "password": "password123"}).status_code == 403
    finally:
        with session_scope() as db:
            for a in admins:
                db.merge(a)
            db.commit()


def select_admins():
    from sqlmodel import select

    from app.db.models import AdminUser

    return select(AdminUser)
