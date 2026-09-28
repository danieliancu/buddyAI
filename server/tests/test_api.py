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
