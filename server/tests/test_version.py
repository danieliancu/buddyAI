"""Which version runs: /healthz (public) and the System page data (operators only)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import version
from app.config import SERVER_DIR, get_settings
from app.main import app
from tests.test_operator_security import _signed_in

SHA = "713a832" + "0" * 33


@pytest.fixture(autouse=True)
def _no_commit_in_environment(monkeypatch):
    monkeypatch.delenv("BUDDYAI_BUILD_COMMIT", raising=False)
    monkeypatch.delenv("SOURCE_COMMIT", raising=False)


def test_commit_falls_back_to_unknown():
    assert version.commit() == "unknown"


def test_commit_comes_from_the_coolify_runtime_variable(monkeypatch):
    monkeypatch.setenv("SOURCE_COMMIT", SHA)
    assert version.commit() == SHA


def test_commit_built_into_the_image_wins(monkeypatch):
    monkeypatch.setenv("BUDDYAI_BUILD_COMMIT", "abcdef1")
    monkeypatch.setenv("SOURCE_COMMIT", SHA)
    assert version.commit() == "abcdef1"


def test_placeholder_in_the_image_does_not_hide_the_runtime_commit(monkeypatch):
    monkeypatch.setenv("BUDDYAI_BUILD_COMMIT", "unknown")
    monkeypatch.setenv("SOURCE_COMMIT", SHA)
    assert version.commit() == SHA


@pytest.mark.parametrize("value", ["", "HEAD", "master", "abc12", "a" * 41, "abcdef1 && curl x", "password=hunter2"])
def test_only_a_commit_id_is_ever_reported(monkeypatch, value):
    monkeypatch.setenv("SOURCE_COMMIT", value)
    assert version.commit() == "unknown"


def test_version_matches_the_project_file():
    assert f'version = "{version.VERSION}"' in (SERVER_DIR / "pyproject.toml").read_text(encoding="utf-8")


def test_healthz_is_public_and_says_only_version_and_commit(monkeypatch):
    monkeypatch.setenv("SOURCE_COMMIT", SHA)
    with TestClient(app) as c:
        r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": version.VERSION, "commit": SHA}


def test_app_title_uses_the_same_version():
    assert app.version == version.VERSION


def test_system_info_shows_version_commit_and_search_backend(monkeypatch):
    monkeypatch.setenv("SOURCE_COMMIT", SHA)
    settings = get_settings()
    monkeypatch.setattr(settings, "search_backend", "ddgs_brave")
    monkeypatch.setattr(settings, "search_legacy_categories", " transport, ,news ")
    monkeypatch.setattr(settings, "brave_api_key", "brave-secret-value-123")
    c, _ = _signed_in("admin")
    r = c.get("/api/system/info")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == version.VERSION and body["commit"] == SHA
    assert body["search_backend"] == "ddgs_brave"
    assert body["search_legacy_categories"] == ["transport", "news"]
    assert "brave-secret-value-123" not in r.text


def test_system_info_needs_an_operator():
    with TestClient(app) as c:
        assert c.get("/api/system/info").status_code == 401


def test_coolify_compose_file_does_not_reference_source_commit():
    """A reference makes Coolify create its own empty variable, and it then stops supplying the real commit."""
    compose = (Path(SERVER_DIR).parent / "deploy" / "docker-compose.coolify.yml").read_text(encoding="utf-8")
    assert "${SOURCE_COMMIT" not in compose and "$SOURCE_COMMIT" not in compose
